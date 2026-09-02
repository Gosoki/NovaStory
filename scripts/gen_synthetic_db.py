#!/usr/bin/env python
"""合成实验库(fixture)—— 按 app 真实落库形状写 N 名被试的 participants / trials /
questionnaires / events,让分析链路在 N=0(未采数)时也能被端到端演练与回归。

形状真源(改了这些文件就要回来对一遍):
  views/screening.py     → participants.screening_json 的键
  views/_trial.py        → trials 的每一列(含 core/llm.current_meta、core/state.round_durations)
  views/guidance.py      → guidance_json = {"rounds":[{round, source, items:[{...is_custom, ai_decided, fallback}]}]}
  views/questionnaire.py → questionnaires 的每一列(分镜标注按 parse_shots 的镜号,解析失败退化为 [{"shot":0}])
  views/_streaming.py    → llm_start / llm_done(payload.elapsed、payload.usage.total_tokens)
  core/state.py          → 每轮事件顺序、attempt 分段;core/db.py → 落库 SQL

⚠️ 这不是真数据:效应是注入的合成值(E>D>C),只能验证管线跑不跑得通,不得进任何结论。

刻意埋进去的难例(少一个,scripts/analysis_smoke.py 就测不到对应缺陷):
  ① 1 名 dev 被试(screening_json.dev=true)——必须被 v3.load / events.load_events 排除
  ② 解析失败的终稿(散文,无分镜标记)→ parse_ok=0、field_completeness=NaN、
     问卷退化为整稿一个标签 [{"shot":0,...}]
  ③ 缺字段 / 非 3 镜的终稿 → field_completeness、shots_ok 有方差(零方差 DV 会让 LMM
     "拟合成功"但 se/p 全 NaN、TOST 除零)
  ④ 被试随机截距:主观量表与用时都带被试效应,否则 LMM 方差成分压边界(会被误当 bug 追)
  ⑤ 重做轮(attempt A 作废 / attempt B 进论文)+ 一次 session_resumed → 事件层切段
  ⑥ E 的 is_custom / ai_decided 逐人不同 → H5 剂量三条腿不会互相抵消
  ⑦ 半截行:1 条 final_output 为 NULL、1 条 script_versions 为 NULL
     (scripts/dev_smoke_e2e.py:325 真会写出这种行)。NULL 读进 pandas 是 nan,而
     `nan or ""` 仍是 nan → 下游 .strip() 会炸,分析链必须扛得住

用法: .venv/bin/python scripts/gen_synthetic_db.py --out /tmp/fake.db [--n 36] [--seed 7]
"""
from __future__ import annotations

import argparse
import json
import random
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_WORDS = "朝 駅 手 影 光 声 風 rain step door 傘 改札".split()
_CAMS = ("クローズアップ", "引き", "動き", "俯瞰")
# 无编号、无【】字段标记 → core.shots.parse_shots 返回 [] (真实的"终稿解析失败")
_PROSE = "主人公は駅で切符を拾い、そのまま歩き出す。誰にも言わない。改札の外は雨だった。"
_BUMP = {"C": -0.3, "D": 0.0, "E": 0.6}       # 注入的条件效应(主观量表)
_BAD_FIELD_P = {"C": 0.25, "D": 0.15, "E": 0.10}  # 掉一个字段的概率 → field_completeness 有方差


def _script(rng: random.Random, n_shots: int = 3, audio: bool = True) -> str:
    """合成一份分镜稿。**两种排版随机各出一半**:

    2026-09-01(§9)之后 prompts 要的是「编号独占一行 → 【画面】一行 → 【秒数】【カメラ】
    【セリフ・音】一行」;旧的一行排版仍会出现在手改稿和换版前的历史数据里。分析链
    (结构完整度 = H4 的终点、逐镜头标注粒度、strip_format → embedding 保真、textstats)
    必须两种都算过 —— 只喂旧排版的话,新排版上的解析退化要等真数据到手才会暴露。"""
    def w() -> str:
        return " ".join(rng.choice(_WORDS) for _ in range(6))
    two_line = rng.random() < 0.5
    out = []
    for i in range(1, n_shots + 1):
        sec, cam = rng.choice([4, 5, 6]), rng.choice(_CAMS)
        if two_line:
            s = f"{i}.\n【画面】{w()}\n【秒数】{sec}秒 【カメラ】{cam}"
            if audio:
                s += f" 【セリフ・音】{w()}"
        else:
            s = f"{i}.【秒数】{sec}秒【カメラ】{cam}【画面】{w()}"
            if audio:
                s += f"【セリフ・音】{w()}"
        out.append(s)
    return "\n".join(out)


def _clip7(x: float) -> int:
    return max(1, min(7, int(round(x))))


class _Clock:
    """事件时间戳游标(ISO 毫秒,与 core/db.insert_event 同格式)。"""

    def __init__(self, start: datetime):
        self.t = start

    def tick(self, seconds: float) -> str:
        self.t += timedelta(seconds=seconds)
        return self.t.isoformat(timespec="milliseconds")


def _insert_event(db, pid: int, ridx: int, ts: str, type_: str,
                  payload: dict | None, seq: int, attempt: str) -> None:
    """与 core/db.insert_event 同列同形,只是 ts 由 fixture 指定(合成时间线)。"""
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO events (participant_id, round_idx, ts, type, payload_json,"
            " seq_in_round, attempt) VALUES (?,?,?,?,?,?,?)",
            (pid, ridx, ts, type_,
             json.dumps(payload, ensure_ascii=False) if payload else None, seq, attempt),
        )


def _llm_pair(group: str, rng: random.Random) -> list[tuple[float, str, dict]]:
    """一次 LLM 调用的事件对(views/_streaming.py 的 llm_start / llm_done)。"""
    el = round(rng.uniform(3, 25), 2)
    p, c = rng.randint(400, 900), rng.randint(300, 800)
    return [(1.5, "llm_start", {"group": group}),
            (el, "llm_done", {"group": group, "elapsed": el,
                              "usage": {"prompt": p, "completion": c, "total_tokens": p + c},
                              "repro": {"seed": 20260901, "system_fingerprint": "fp_smoke"}})]


def _round_events(cond: str, g_rounds: list[dict], rng: random.Random
                  ) -> list[tuple[float, str, dict | None]]:
    """一轮的事件流 (距上一条的秒数, type, payload),顺序照 views/ 的真实调用顺序。"""
    ev: list[tuple[float, str, dict | None]] = [
        (0.0, "round_start", None),
        (round(rng.uniform(20, 60), 2), "intent_submit", {"chars": rng.randint(12, 80)}),
    ]
    if cond == "E":
        for k, rd in enumerate(g_rounds):  # views/guidance.py:_generate_questions → _finish_round
            if k:
                ev.append((round(rng.uniform(5, 30), 2), "continue_guidance_click", None))
            ev += _llm_pair("E-guidance-r1" if k == 0 else "E-guidance-fu", rng)
            ev.append((0.3, "guidance_shown",
                       {"round": rd["round"], "source": rd["source"],
                        "n_questions": len(rd["items"]), "fallback": False}))
            for i in range(len(rd["items"])):
                ev.append((round(rng.uniform(4, 20), 2), "guidance_answer_saved",
                           {"round": rd["round"], "q": i}))
            ev += _llm_pair("E-final" if k == 0 else "E-revise", rng)
            for it in rd["items"]:  # 生成成功后才补记每题的答法
                ev.append((0.05, "guidance_answer", {"dimension": it["dimension"],
                                                     "is_custom": it["is_custom"],
                                                     "ai_decided": it["ai_decided"]}))
            ev.append((0.2, "guidance_submit", {"round": rd["round"]}))
            ev.append((0.2, "script_shown", {"v": k + 1}))
    else:
        ev += _llm_pair("C-oneshot" if cond == "C" else "D-first", rng)
        ev.append((0.2, "script_shown", {"v": 1}))
        if cond == "D":
            for k in range(rng.randint(1, 3)):
                ev.append((round(rng.uniform(20, 60), 2), "revision_request",
                           {"chars": rng.randint(5, 40)}))
                ev += _llm_pair("D-revise", rng)
                ev.append((0.2, "script_shown", {"v": k + 2}))
    if cond != "C":
        ev.append((round(rng.uniform(15, 90), 2), "hand_edit_saved",
                   {"v": 9, "chars_delta": rng.randint(5, 200)}))
    ev.append((round(rng.uniform(5, 40), 2), "trial_submit", None))
    return ev


def _guidance_items(rng: random.Random, dims: list[str], custom_p: float, ai_p: float,
                    rnd: int, source: str) -> dict:
    """一轮引导的 items —— 自填/交给AI 的比例逐人不同(否则 H5 三条腿完全共线)。"""
    items = []
    for d in dims:
        is_custom = rng.random() < custom_p
        ai_decided = (not is_custom) and rng.random() < ai_p
        items.append({
            "dimension": d, "question": f"{d} についてどうする?",
            "options": [] if is_custom else ["A 案", "B 案", "C 案"],
            "chosen": "自分で書いた答え" if is_custom else ("" if ai_decided else "A 案"),
            "is_custom": is_custom, "ai_decided": ai_decided, "fallback": False,
        })
    entry = {"round": rnd, "source": source, "items": items}
    if source == "ai_from_draft":
        entry["draft_snapshot_ref"] = 1
    return entry


def generate(out: Path, n: int = 36, seed: int = 7) -> dict:
    """写一个 N=n(外加 1 名 dev)的合成库,返回给回归测试断言用的清单。"""
    if out.exists():
        out.unlink()
    for suffix in ("-wal", "-shm"):
        p = out.with_name(out.name + suffix)
        if p.exists():
            p.unlink()

    from core import db, state          # noqa: PLC0415 —— 必须在改 DB_PATH 前不建连接
    from core.shots import parse_shots  # noqa: PLC0415
    db.DB_PATH = out
    db.init_db()

    rng = random.Random(seed)
    topics = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))[:3]
    dims = ["psychology", "turning_point", "key_shot", "tone", "ending", "sound"]
    t0 = datetime(2026, 9, 1, 10, 0, 0)

    dev_pid, parse_fail, redo, resumed = None, [], [], []
    null_final, null_versions = [], []
    dev_after = min(5, n)                       # dev 被试插在中间:验证它不挪真被试的拉丁方
    for p in range(n + 1):
        is_dev = (p == dev_after)
        idx = p if p < dev_after else p - 1      # 真被试的序号(dev 不占号)
        novice = rng.random() < 0.7
        screening = {
            "published_idx": 0 if novice else 1, "background": "no" if novice else "yes",
            "written": "no", "self_rating": 2 if novice else 4, "aiexp_idx": rng.randint(0, 2),
            "trust": rng.randint(2, 6), "own_trait": rng.randint(2, 6),
            "quiz1_idx": 0, "quiz2_idx": 1, "quiz_correct": 1 if novice else 2,
            "is_novice": novice,
        }
        demographics = {"age_idx": rng.randint(0, 4), "gender_idx": rng.randint(0, 3),
                        "ai_freq_idx": rng.randint(0, 3)}
        if is_dev:  # views/devtools.py _skip_intake 的形状
            screening.update({"dev": True, "is_novice": True})
            demographics["dev"] = True
        pid, seq, _tok = db.insert_participant("ja", demographics, screening, passed=True)
        if is_dev:
            dev_pid = pid
        db.update_participant(pid, status="done", attention_ok=1, attention_raw=2,
                              completion_code=secrets.token_hex(4).upper(),
                              final_survey_json=json.dumps(
                                  {"pref_round": rng.randint(1, 3), "reuse_round": rng.randint(1, 3),
                                   "overall_sat": rng.randint(3, 7)}))

        sub = rng.gauss(0, 0.7)      # 被试随机截距(主观量表)
        t_sub = rng.gauss(0, 12)     # 被试随机截距(用时,秒)
        custom_p, ai_p = rng.uniform(0.1, 0.8), rng.uniform(0.05, 0.5)
        clock = _Clock(t0 + timedelta(days=p))
        for ridx, step in enumerate(state.plan_for_seq(seq, topics), start=1):
            cond, topic = step["condition"], step["topic"]

            # ---- 终稿:多数正常,少数缺字段/非 3 镜,前两名真被试各埋 1 条解析失败 ----
            fail = (not is_dev) and idx < 2 and ridx == 1
            if fail:
                final = _PROSE
                parse_fail.append((pid, ridx))
            else:
                n_shots = 3 if rng.random() > 0.10 else rng.choice([2, 4])
                final = _script(rng, n_shots, audio=rng.random() > _BAD_FIELD_P[cond])
            # C 一次成稿(终稿 = 首个 AI 版);D/E 有人改 → 首版与终稿不同
            first_ai = final if cond == "C" else _script(rng)
            versions = [{"v": 1, "author": "ai", "text": first_ai}]
            if cond != "C":
                versions.append({"v": 2, "author": "user_edit", "text": final})
            # 半截行:第 1 名真被试的第 2 轮丢终稿、第 3 轮丢版本历史(埋点⑦)
            sv = json.dumps(versions, ensure_ascii=False)
            if (not is_dev) and idx == 0 and ridx == 2:
                final = None
                null_final.append((pid, ridx))
            elif (not is_dev) and idx == 0 and ridx == 3:
                sv = None
                null_versions.append((pid, ridx))

            g_json, rounds = None, []
            if cond == "E":
                n_items, n_items2 = rng.randint(3, 6), rng.randint(0, 2)
                rounds = [_guidance_items(rng, dims[:n_items], custom_p, ai_p,
                                          1, "fixed3+ai_supplement")]
                if n_items2:
                    rounds.append(_guidance_items(rng, dims[:n_items2], custom_p, ai_p,
                                                  2, "ai_from_draft"))
                g_json = json.dumps({"rounds": rounds}, ensure_ascii=False)

            # ---- 事件流:第 3 名真被试的第 1 轮先做一段作废的 attempt(重做轮切段)----
            attempt = secrets.token_hex(4)
            n_ev = 0
            if (not is_dev) and idx == 2 and ridx == 1:
                dead = secrets.token_hex(4)
                for i, (gap, ty, pl) in enumerate(_round_events(cond, rounds, rng)[:6], 1):
                    _insert_event(db, pid, ridx, clock.tick(gap), ty, pl, i, dead)
                redo.append((pid, ridx))
            if (not is_dev) and idx == 3 and ridx == 2:
                _insert_event(db, pid, ridx, clock.tick(30), "session_resumed",
                              {"round_idx": ridx}, 1, attempt)
                n_ev = 1
                resumed.append((pid, ridx))
            for gap, ty, pl in _round_events(cond, rounds, rng):
                n_ev += 1
                _insert_event(db, pid, ridx, clock.tick(gap), ty, pl, n_ev, attempt)

            post = max(1.0, rng.gauss({"C": 12, "D": 75, "E": 30}[cond],
                                      {"C": 5, "D": 25, "E": 12}[cond]) + t_sub)
            trial_id = db.insert_trial(
                participant_id=pid, round_idx=ridx, condition=cond,
                topic_json=json.dumps(topic, ensure_ascii=False),
                intent_statement=f"参加者{pid}の意図 {rng.random():.3f}",
                final_output=final, parse_ok=int(bool(parse_shots(final))),
                guidance_json=g_json,
                revision_requests=(json.dumps([{"round": 1, "text": "もっと明るく"}],
                                              ensure_ascii=False) if cond == "D" else None),
                script_versions=sv,
                n_ai_rounds={"C": 0, "D": rng.randint(1, 3), "E": len(rounds) - 1}[cond],
                n_hand_edits=0 if cond == "C" else 1,
                hand_edit_chars=0 if cond == "C" else rng.randint(5, 200),
                model="gpt-4o-mini-2024-07-18", temperature=0.8,
                base_url="https://api.openai.com/v1",
                t_read_intent=round(rng.uniform(20, 60), 2),
                t_pregen=round(max(5.0, rng.gauss(50, 15) + t_sub), 2) if cond == "E" else None,
                t_postgen=round(post, 2),
                t_llm_wait=round(rng.uniform(5, 30), 2),
                t_total=round(rng.uniform(120, 300), 2),
            )
            # LOG4:trial 落库后才给"进了论文"的那段事件盖 trial_id(作废段留 NULL)
            db.attach_trial_to_events(trial_id, pid, ridx, attempt)
            n_ev += 1
            _insert_event(db, pid, ridx, clock.tick(rng.uniform(40, 120)),
                          "questionnaire_submit", None, n_ev, attempt)
            if ridx == 3:  # 全程问卷:在最后一轮的 attempt 里,round_idx=N_ROUNDS
                n_ev += 1
                _insert_event(db, pid, ridx, clock.tick(rng.uniform(30, 90)),
                              "final_survey_submit", None, n_ev, attempt)

            # ---- 问卷:分镜标注按真实镜号;解析失败退化为整稿一个标签 ----
            shots = parse_shots(final)
            tag_w = [3 + (3 if cond == "E" else 0), 4, 2]  # mine / ai_ok / ai_against
            ann = [{"shot": s["idx"],
                    "tag": rng.choices(["mine", "ai_ok", "ai_against"], tag_w)[0]}
                   for s in shots] or \
                  [{"shot": 0, "tag": rng.choices(["mine", "ai_ok", "ai_against"], tag_w)[0]}]
            bump = _BUMP[cond]
            db.insert_questionnaire(
                participant_id=pid, round_idx=ridx,
                ownership_json=json.dumps(
                    {f"own{i}": _clip7(4 + bump + sub + rng.gauss(0, 0.8)) for i in (1, 2, 3)}),
                soa_json=json.dumps(
                    {f"soa{i}": _clip7(4 + bump + sub + rng.gauss(0, 0.9)) for i in (1, 2)}),
                tlx_json=json.dumps({"tlx1": _clip7(4 + sub * 0.3 + rng.gauss(0, 1))}),
                intent_violation=_clip7(4 - bump + sub * 0.5 + rng.gauss(0, 1)),
                imagine_match=_clip7(4 + bump + sub + rng.gauss(0, 1)),
                satisfaction=_clip7(4 + bump + sub + rng.gauss(0, 1)),
                ai_q_quality=_clip7(5 + sub * 0.5 + rng.gauss(0, 1)) if cond == "E" else None,
                ai_q_amount=_clip7(4 + rng.gauss(0, 1)) if cond == "E" else None,
                ai_q_best_json=(json.dumps([{"idx": 0, "dimension": dims[0],
                                             "question": "Q0", "chosen": "A 案"}],
                                           ensure_ascii=False) if cond == "E" else None),
                shot_annotations_json=json.dumps(ann, ensure_ascii=False),
            )

    with db._conn() as conn:
        n_ev = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return {"db": out, "n": n, "dev_participant_id": dev_pid,
            "trials_total": (n + 1) * 3, "trials_non_dev": n * 3,
            "events": n_ev, "parse_fail": parse_fail,
            "redo_rounds": redo, "resumed_rounds": resumed,
            "null_final": null_final, "null_versions": null_versions}


def main() -> None:
    ap = argparse.ArgumentParser(description="合成实验库 fixture(非真数据)")
    ap.add_argument("--out", type=Path, required=True, help="输出 .db 路径(会覆盖)")
    ap.add_argument("--n", type=int, default=36, help="真被试人数(另加 1 名 dev)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    info = generate(args.out, args.n, args.seed)
    print(f"合成库 → {info['db']}")
    print(f"  被试 {info['n']} + dev 1(id={info['dev_participant_id']}) · "
          f"trials {info['trials_total']}(非 dev {info['trials_non_dev']}) · "
          f"events {info['events']}")
    print(f"  埋点:解析失败 {info['parse_fail']} · 重做轮 {info['redo_rounds']} · "
          f"续接 {info['resumed_rounds']}")
    print(f"  半截行:缺终稿 {info['null_final']} · 缺版本历史 {info['null_versions']}")


if __name__ == "__main__":
    main()
