"""研究员后台的「实验数据管理 · 监控」面板 —— 概览卡片 + 采数进度/平衡统计图。
用 Streamlit 原生图(浏览器渲染,日语标签不糊)。仅研究员可见。"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from analysis import prereg
from core import config, db
from i18n import t

_TARGET_N = 36


def _cond_label(c: str) -> str:
    return t(f"monitor.cond_{c}") if c in ("C", "D", "E") else c


def _loads(x) -> dict:
    if not isinstance(x, str) or not x.strip():
        return {}
    try:
        return json.loads(x)
    except (ValueError, TypeError):
        return {}


def _topic_title(x) -> str:
    title = _loads(x).get("title", "")   # 别叫 t:会遮蔽上面 import 的翻译函数
    if isinstance(title, dict):
        return title.get("ja") or title.get("zh") or "?"
    return title or "?"


def _novice_flags(parts: pd.DataFrame) -> pd.Series:
    """每位被试是否 novice —— 与分析侧一样从原始 5 项**重算**(prereg.is_novice),
    不信任入库时写下的布尔:NOVICE_MIN_CRITERIA 若按退路从 5 调到 4,旧布尔就是旧口径。"""
    if "screening_json" not in parts:
        return pd.Series(False, index=parts.index)
    return parts["screening_json"].map(lambda x: prereg.is_novice(_loads(x)))


def render() -> None:
    st.subheader(t("monitor.title"))
    parts = db.load_table("participants")
    trials = db.load_table("trials")
    # 研究员用 devtools「跳过同意+筛查」注入的测试行不进任何一格:它们在 seq 计数里
    # 已被排除(core/db.insert_participant),在分析里被排除(v3.included_participants),
    # 面板若还把它们算进完成数 / seq 平衡 / novice 占比,研究员看到的就是三套口径。
    dev_ids: set[int] = set()
    if "screening_json" in parts:
        is_dev = parts["screening_json"].map(lambda x: bool(_loads(x).get("dev")))
        dev_ids = {int(i) for i in parts.loc[is_dev, "id"]}
        parts = parts[~is_dev]
    if dev_ids:
        st.caption(t("monitor.dev_excluded", n=len(dev_ids)))
        if "participant_id" in trials:
            trials = trials[~trials["participant_id"].isin(dev_ids)]
    if parts.empty:
        st.info(t("monitor.empty"))
        return

    _overview(parts)
    _sessions(parts, trials)
    _data_health(trials, dev_ids)
    _usage(dev_ids)
    _balance(trials)
    _progress(parts)
    _descriptive(trials)


def _overview(parts: pd.DataFrame) -> None:
    status = parts["status"].value_counts() if "status" in parts else pd.Series(dtype=int)
    done, inprog, out = (int(status.get(k, 0)) for k in ("done", "in_progress", "screened_out"))
    done_rows = parts[parts["status"] == "done"] if "status" in parts else parts.iloc[0:0]
    nov_flags = _novice_flags(done_rows).astype(bool)   # 空表时 map 得到 object 空列,.sum() 会给 ''
    nov = float(nov_flags.mean()) if len(done_rows) else float("nan")
    with st.container(border=True):
        c = st.columns(4)
        c[0].metric(t("monitor.done"), done)
        c[1].metric(t("monitor.inprog"), inprog)
        c[2].metric(t("monitor.out"), out)
        c[3].metric(t("monitor.novice_share"), "—" if pd.isna(nov) else f"{nov:.0%}")
    st.progress(min(done / _TARGET_N, 1.0),
                text=t("monitor.progress", n=_TARGET_N, done=done,
                       remain=max(_TARGET_N - done, 0)))
    # 2026-09-07 拍板:主分析人群 = 全样本 → **招募够不够看上面那条进度条**。
    # 下面这个 novice 人数只是样本经验构成的参考,决定的是事后探索性切分还有没有解释力。
    st.caption(t("monitor.novice_done", n=int(nov_flags.sum())))
    # 语言构成:ja=日本队列、zh=中国队列,两者都是正式数据(2026-09-06 拍板,分析时按
    # lang 分开即可);只有 en 才是研究员测试或脱离协议的会话 —— 采数期就要看见,
    # 不能等到分析时才发现(那时已无法补救)。
    if "lang" in parts and len(parts):
        mix = parts["lang"].fillna("ja").value_counts().to_dict()
        line = " / ".join(f"{k}: {v}" for k, v in mix.items())
        (st.caption if set(mix) <= {"ja", "zh"} else st.warning)(
            t("monitor.lang_mix", mix=line))
    _dup_contacts(parts)


def _jst(x) -> str:
    """库里是不带时区的服务器本地时间(UTC);面板一律按被试所在的 JST 显示。"""
    ts = pd.to_datetime(x, errors="coerce")
    return "—" if pd.isna(ts) else (ts + pd.Timedelta(hours=9)).strftime("%m-%d %H:%M")


def _sessions(parts: pd.DataFrame, trials: pd.DataFrame) -> None:
    """在跑的会话:开始时间、最后活动、已交轮数,以及释放序号的入口。

    为什么需要它:拉丁方序号在**通过筛查那一刻**就被占住,而且没有任何自动回收 ——
    关掉页面再也不回来的人,序号照占。18 个 Williams 序列要平衡就得让每格人数相等,
    被弃号占着的格子会一直缺人。释放把该行标成 dev,序号让给下一位(见 db.release_participant)。
    """
    if "status" not in parts:
        return
    live = parts[parts["status"] == "in_progress"].copy()
    st.markdown(f"**{t('monitor.sessions_title')}**")

    # 当前真正连着的 WebSocket 数(内部 API,拿不到就不显示 —— 它只是参考,不影响功能)
    try:
        from streamlit.runtime import get_instance
        n_ws = len(get_instance()._session_mgr.list_active_sessions())
        st.caption(t("monitor.active_ws", n=n_ws))
    except Exception:  # noqa: BLE001
        pass

    if live.empty:
        st.caption(t("monitor.no_inprog"))
        return

    ev = db.load_table("events")
    last_seen = (ev.groupby("participant_id")["ts"].max()
                 if {"participant_id", "ts"} <= set(ev.columns) else pd.Series(dtype=str))
    n_rounds = (trials.groupby("participant_id").size()
                if "participant_id" in trials else pd.Series(dtype=int))

    st.caption(t("monitor.sessions_hint"))
    hdr = st.columns([1, 1, 2.2, 2.2, 1.2, 1.4])
    for col, key in zip(hdr, ("col_id", "col_seq", "col_start", "col_last",
                              "col_rounds", "col_action")):
        col.caption(t(f"monitor.{key}"))
    for _, r in live.sort_values("created_at").iterrows():
        pid = int(r["id"])
        c = st.columns([1, 1, 2.2, 2.2, 1.2, 1.4])
        c[0].write(f"#{pid}")
        c[1].write("—" if pd.isna(r.get("seq")) else str(int(r["seq"])))
        # 与本面板的每日完成图同口径:库里存的是 UTC,这里 +9h 显示成被试所在的日本时间,
        # 否则研究员看到的「最后活动」会比实际早 9 小时,判断人走没走时会误判。
        c[2].write(_jst(r.get("created_at")))
        c[3].write(_jst(last_seen.get(pid)))
        c[4].write(f"{int(n_rounds.get(pid, 0))}/{config.N_ROUNDS}")
        with c[5].popover(t("monitor.release")):
            st.caption(t("monitor.release_help"))
            if st.button(t("monitor.release_confirm"), key=f"_rel_{pid}",
                         type="primary", width="stretch"):
                db.release_participant(pid)
                st.rerun()


def _dup_contacts(parts: pd.DataFrame) -> None:
    """同一个邮箱出现在多行 = 很可能是同一个人重复参加。

    完成页在「请勿重复参加」的正下方摆了一支免费短片作为回报,而代码里**没有任何去重**
    (`insert_participant` 无条件 passed=True)。重复参加会吃掉多个 Williams seq、
    破坏被试内 LMM 的独立性假设,还会让同一个人在主分析人群(全样本)里算好几次。
    邮箱是唯一能照出这件事的信号,所以在这里**只报计数、不显示地址** —— 既让研究员
    看得见,又不把那一列重新暴露到界面上。"""
    if "contact_json" not in parts.columns:
        return
    mails = (parts["contact_json"].dropna().map(lambda x: _loads(x).get("email", ""))
             .map(lambda x: x.strip().lower()))
    mails = mails[mails != ""]
    if mails.empty:
        return
    dup = int((mails.value_counts() > 1).sum())
    if dup:
        st.warning(t("monitor.dup_contact", n=dup))


def _data_health(trials: pd.DataFrame, dev_ids: set[int] = frozenset()) -> None:
    """Live data-quality signals so a silent corruption (parse failures, LLM
    errors, guidance fallbacks, slow gens) is visible between sessions instead of
    a green progress bar hiding a broken primary DV (deep-review 2026-07-19, #30)."""
    ev = db.load_table("events")
    if dev_ids and "participant_id" in ev:
        ev = ev[~ev["participant_id"].isin(dev_ids)]
    st.markdown(f"**{t('monitor.health_title')}**")
    if ev.empty and trials.empty:
        st.caption(t("monitor.health_none"))
        return
    typ = ev["type"] if "type" in ev else pd.Series(dtype=str)
    pj = ev["payload_json"] if "payload_json" in ev else pd.Series(dtype=str)

    # parse_ok 为 NULL(老行 / 未回填)时,`== 0` 得 False,会被算成"解析成功" —— 于是
    # 整列全空也显示 0%,把"没测到"说成"没问题"。先转数值,全空就显示「—」。
    po = pd.to_numeric(trials["parse_ok"], errors="coerce") if "parse_ok" in trials else pd.Series(dtype=float)
    parse_fail = float((po == 0).mean()) if po.notna().any() else float("nan")

    n_start = int((typ == "llm_start").sum())
    n_done = int((typ == "llm_done").sum())
    n_err = int((typ == "llm_error").sum())
    err_rate = (n_err / n_start) if n_start else float("nan")
    # **发起了却没有任何终止事件**的调用。失控重试正是这个形态:被试(或断线)反复触发
    # 生成,每次 rerun 打断上一次流式调用并重新发起,于是只留下 llm_start ——
    # 一条 llm_error 都没有,按 err_rate 看是 0% 完美。生产库里已经真实发生过一次:
    # 单人 7 分钟 86 条 llm_start 对 1 条 llm_done,而面板当时显示错误率 0%。
    unclosed = max(n_start - n_done - n_err, 0)
    unclosed_rate = (unclosed / n_start) if n_start else float("nan")

    # 配图成功率。core/imagegen.py 的 worker 用 `except Exception: pass` 吞掉一切失败
    # (429 限额、超时、内容策略),只写一个 .done 标记就算"试过了" —— 被试看到的是空白画框,
    # 而这里此前一个字都不显示。配图是主观 DV 的测量情境(被试答题时看着它),
    # 大批失败必须在采数期就看见,不能等分析时才从 images_ready 里对出来。
    img = pj[typ == "images_ready"].map(_loads)
    n_shot_tot = sum(int(p.get("n_shots") or 0) for p in img)
    n_shot_ok = sum(int(p.get("n_ok") or 0) for p in img)
    img_rate = (n_shot_ok / n_shot_tot) if n_shot_tot else float("nan")
    gs = pj[typ == "guidance_shown"]
    fb_rate = gs.map(lambda x: bool(_loads(x).get("fallback"))).mean() if len(gs) else float("nan")
    done_pay = pj[typ == "llm_done"].map(_loads)
    # 与 analysis/events 同口径:正式模型不返回 system_fingerprint,退到 served_model,
    # 否则这条告警永远不会触发(而它正是采数中途换模型的唯一预警)。
    fps = {((p.get("repro") or {}).get("system_fingerprint")
            or (p.get("repro") or {}).get("served_model")) for p in done_pay} - {None}
    el = done_pay.map(lambda p: p.get("elapsed"))
    el = pd.to_numeric(el, errors="coerce").dropna()
    med, p95 = (el.median(), el.quantile(0.95)) if len(el) else (float("nan"), float("nan"))

    def _pct(x):
        return "—" if pd.isna(x) else f"{x:.0%}"
    c = st.columns(6)
    c[0].metric(t("monitor.health_parse"), _pct(parse_fail))
    c[1].metric(t("monitor.health_llm_err"), _pct(err_rate))
    c[2].metric(t("monitor.health_unclosed"), _pct(unclosed_rate))
    c[3].metric(t("monitor.health_images"), _pct(img_rate))
    c[4].metric(t("monitor.health_fallback"), _pct(fb_rate))
    c[5].metric(t("monitor.health_latency"),
                "—" if pd.isna(med) else f"{med:.0f}/{p95:.0f}s")
    # 掉到八成以下多半是撞了 gpt-image-1-mini 的分钟限额(30 人同步提交时约 45 张/分钟)。
    if n_shot_tot >= 6 and img_rate < 0.8:
        st.warning(t("monitor.images_warn", ok=n_shot_ok, total=n_shot_tot))
    # 少量未闭合是正常的(被试正在生成中、或刚好关了页面);持续偏高说明有人在反复触发。
    if n_start >= 10 and unclosed_rate > 0.25:
        st.warning(t("monitor.unclosed_warn", n=unclosed, total=n_start))
    if len(fps) > 1:
        # B7:服务端 build 在采数中途换了 —— 输出本身不可逐字复现,这是唯一能报告的漂移证据
        st.warning(t("monitor.fingerprint_drift", n=len(fps)))


def _usage(dev_ids: set[int] = frozenset()) -> None:
    """API 用量。数据一直在记(llm_done 的 payload.usage 由 core/llm._stash_usage 塞进去),
    只是此前面板一个字都不显示 —— 采数期看不到用量,撞限额或跑超预算都只能事后从账单发现。

    ⚠️ 只覆盖**文本**调用。配图走 core/imagegen 的另一条路,那边不记 usage,
    所以这里用「已生成张数」代替(单价见 imagegen 的选型注释,约 $0.0022/张)。"""
    ev = db.load_table("events")
    if ev.empty or "type" not in ev:
        return
    if dev_ids and "participant_id" in ev:
        ev = ev[~ev["participant_id"].isin(dev_ids)]
    pj = ev["payload_json"] if "payload_json" in ev else pd.Series(dtype=str)
    done_pay = pj[ev["type"] == "llm_done"].map(_loads)
    if done_pay.empty:
        return

    st.markdown(f"**{t('monitor.usage_title')}**")
    p_tok = sum(int((d.get("usage") or {}).get("prompt") or 0) for d in done_pay)
    c_tok = sum(int((d.get("usage") or {}).get("completion") or 0) for d in done_pay)
    n_call = len(done_pay)
    img = pj[ev["type"] == "images_ready"].map(_loads)
    n_img = sum(int(d.get("n_ok") or 0) for d in img)
    # 人均必须**分子分母同源**:总量含未完成者的消耗,拿它去除以完成人数会把人均抬得离谱
    # (实测归档库:25 次调用 ÷ 3 位完成者 = 8.3 次/人,而完成者实际只用了 8 次)。
    # 所以人均单独用「完成者自己的事件」重算一遍。
    parts = db.load_table("participants")
    done_ids = (set(parts.loc[parts["status"] == "done", "id"].astype(int))
                if "status" in parts and "id" in parts else set())
    n_done = len(done_ids)
    if n_done and "participant_id" in ev:
        mine = ev["participant_id"].isin(done_ids)
        d_pay = pj[mine & (ev["type"] == "llm_done")].map(_loads)
        d_img = pj[mine & (ev["type"] == "images_ready")].map(_loads)
        h_calls = len(d_pay)
        h_tok = sum(int((d.get("usage") or {}).get("prompt") or 0)
                    + int((d.get("usage") or {}).get("completion") or 0) for d in d_pay)
        h_img = sum(int(d.get("n_ok") or 0) for d in d_img)
    else:
        h_calls = h_tok = h_img = 0

    c = st.columns(4)
    c[0].metric(t("monitor.usage_calls"), f"{n_call:,}")
    c[1].metric(t("monitor.usage_prompt"), f"{p_tok:,}")
    c[2].metric(t("monitor.usage_completion"), f"{c_tok:,}")
    c[3].metric(t("monitor.usage_images"), f"{n_img:,}")
    if n_done:
        st.caption(t("monitor.usage_per_head", n=n_done,
                     calls=f"{h_calls / n_done:.1f}",
                     tok=f"{h_tok / n_done:,.0f}",
                     img=f"{h_img / n_done:.1f}"))
    # 按调用类型拆开:E 的引导是额外一轮调用,想知道 E 贵多少就看这里
    by_group = pd.Series([d.get("group", "?") for d in done_pay]).value_counts()
    if len(by_group):
        st.caption(t("monitor.usage_by_group",
                     detail=" · ".join(f"{k} {v}" for k, v in by_group.items())))
    st.caption(t("monitor.usage_note"))


def _balance(trials: pd.DataFrame) -> None:
    if trials.empty:
        return
    col_topic, col_cond = t("monitor.col_topic"), t("monitor.col_cond")
    df = trials.copy()
    df[col_topic] = df["topic_json"].map(_topic_title)
    df[col_cond] = df["condition"].map(_cond_label)
    piv = df.pivot_table(index=col_cond, columns=col_topic, values="participant_id",
                         aggfunc="count", fill_value=0)
    st.markdown(t("monitor.balance_title"))
    try:
        st.dataframe(piv.style.background_gradient(cmap="Greens", axis=None), width="stretch")
    except Exception:  # noqa: BLE001
        st.dataframe(piv, width="stretch")


def _progress(parts: pd.DataFrame) -> None:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(t("monitor.seq_title"))
        if "seq" in parts and "status" in parts:
            # Balance the DATA THAT COUNTS: completed (status=='done') per seq.
            # Show all 18 cells (missing → 0) so laggards are visible; keep
            # recruiting until each hits target (over-recruit absorbs dropouts, #21).
            done = parts[parts["status"] == "done"]
            seqc = done["seq"].dropna().astype(int).value_counts()
            seqc = seqc.reindex(range(config.LATIN_SQUARE_N), fill_value=0)
            seqc.index = seqc.index.map(lambda i: f"seq{i}")
            st.bar_chart(seqc, height=220)
            st.caption(t("monitor.seq_target", n=_TARGET_N // config.LATIN_SQUARE_N))
        else:
            st.caption(t("monitor.no_seq"))
    with c2:
        st.markdown(t("monitor.daily_title"))
        if "created_at" in parts and (parts.get("status") == "done").any():
            date_col = t("monitor.col_date")
            d = parts[parts["status"] == "done"].copy()
            # 库里的时间戳是 datetime.now() 写的服务器本地时间,而这台机器是 Etc/UTC;
            # 被试在日本(UTC+9)。直接按 UTC 日期分组的话,日本时间 09:00 之前完成的人
            # 会被算进前一天 —— 每天的完成人数就都是错的。按 JST 归日再统计。
            # (只影响这张按日图:所有时长指标都是两个时间戳相减,与时区无关。)
            d[date_col] = (pd.to_datetime(d["created_at"], errors="coerce")
                           + pd.Timedelta(hours=9)).dt.date
            daily = d.dropna(subset=[date_col]).groupby(date_col).size()
            st.bar_chart(daily, height=220) if len(daily) else st.caption(t("monitor.none"))
        else:
            st.caption(t("monitor.no_done"))


def _descriptive(trials: pd.DataFrame) -> None:
    quest = db.load_table("questionnaires")
    if quest.empty or trials.empty:
        return
    cond = trials[["participant_id", "round_idx", "condition"]]
    q = quest.merge(cond, on=["participant_id", "round_idx"], how="left").dropna(subset=["condition"])
    if q.empty:
        return

    def _own(x):
        d = _loads(x)
        vs = [d.get(f"own{i}") for i in (1, 2, 3) if d.get(f"own{i}") is not None]
        return sum(vs) / len(vs) if vs else None

    own_col = t("monitor.ownership")
    if "ownership_json" in q:
        q[own_col] = q["ownership_json"].map(_own)
    cols = [c for c in ("satisfaction", "imagine_match", own_col) if c in q]
    if not cols:
        return
    agg = q.groupby("condition")[cols].mean(numeric_only=True)
    agg = agg.rename(columns={"satisfaction": t("monitor.satisfaction"),
                              "imagine_match": t("monitor.imagine")})
    agg.index = agg.index.map(_cond_label)
    st.markdown(t("monitor.descriptive_title"))
    st.bar_chart(agg, height=260)
