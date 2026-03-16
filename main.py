"""
main.py
한국주식 패턴 알림 텔레그램 봇
- ① 15:40 자동 스캔 (KOSPI+KOSDAQ 중소형주)
- ② 수동: /scan, "지금 스캔해줘"
"""

import asyncio
import os
import subprocess
import sys
import threading

import anthropic
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 프로젝트 루트의 .env 로드 (AI-stockAlarm/../.env)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))

CLAUDE_BIN = "/home/wputer/.local/bin/claude"
_cwd = _ROOT  # claude 실행 기준 디렉토리
_model = "claude"  # "claude" | "ollama"
OLLAMA_MODEL = "qwen2.5:14b"

def _model_prefix() -> str:
    """응답 메시지 앞에 붙일 모델명 prefix."""
    if _model == "ollama":
        # qwen2.5:14b → qwen2.5-14b 형식 정리
        label = OLLAMA_MODEL.replace(":", "-")
        return f"(ollama:{label})\n"
    # claude CLI → 버전 파싱 시도, 실패 시 기본값
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "--version"],
            capture_output=True, text=True, timeout=5,
            env={k: v for k, v in os.environ.items() if k != "CLAUDECODE"},
        )
        ver_line = (r.stdout + r.stderr).strip().split("\n")[0].lower()
        if "haiku" in ver_line:
            label = "haiku"
        elif "opus" in ver_line:
            label = "opus"
        else:
            label = "sonnet"
    except Exception:
        label = "sonnet"
    return f"(claude:{label})\n"

# AI-stockAlarm 디렉토리를 Python 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher import get_candidates_cached, get_stock_name
from scanner import scan_all, scan_all_plus, format_result
from watchlist import update_from_scan, add_stock, remove_stock, get_all
from sector_info import enrich_results
import database
from web_server import app as web_app

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
USER_ID = int(os.getenv("TELEGRAM_USER_ID", "0"))
CLAUDE_KEY = os.getenv("CLAUDE_API_KEY")
NGROK_TOKEN = os.getenv("NGROK_AUTH_TOKEN", "")

claude_client = anthropic.Anthropic(api_key=CLAUDE_KEY)


# ── 권한 체크 데코레이터 ──────────────────────────────────────────────

def authorized(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != USER_ID:
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper


# ── 핵심 작업 ─────────────────────────────────────────────────────────

async def do_scan(app: Application, is_manual: bool = False) -> None:
    """패턴 스캔 실행 (① 자동 / ③ 수동 공용)"""
    prefix = "🔍 수동" if is_manual else "🤖 자동"
    msg = await app.bot.send_message(
        chat_id=USER_ID,
        text=f"{prefix} 스캔 시작\\!\n⏳ 후보 종목 수집 중\\.\\.\\.\n\\(첫 실행 시 약 2\\~3분 소요\\)",
        parse_mode="MarkdownV2",
    )

    try:
        # 1. 후보 종목 수집 (캐시 활용, 수동 스캔은 강제 갱신)
        tickers: list[str] = await asyncio.to_thread(
            get_candidates_cached,
            70,
            is_manual,  # 수동 스캔은 캐시 무시
        )

        await msg.edit_text(
            f"{prefix} 스캔 중\\.\\.\\.\n"
            f"📋 후보: {len(tickers)}개 종목\n"
            f"⏳ 패턴 탐지 중\\.\\.\\.",
            parse_mode="MarkdownV2",
        )

        # 2. 패턴 탐지 (기존)
        results: list[dict] = await asyncio.to_thread(scan_all, tickers)

        # 3. 업종/테마 보강 — AI 자원 절약을 위해 비활성화
        # if results:
        #     await msg.edit_text(f"✅ {len(results)}개 패턴 감지! 업종/테마 분석 중...")
        #     results = await asyncio.to_thread(enrich_results, results, claude_client)

        # 4. Plus 스캔 (로컬 DB 종목) — Plus1/Plus2는 대시보드 탭에서 온디맨드 실행
        plus_results: list[dict] = []
        try:
            import data_store
            local_tickers = await asyncio.to_thread(data_store.get_all_tickers)
            if local_tickers:
                plus_results = await asyncio.to_thread(scan_all_plus, local_tickers)
        except Exception as e:
            print(f"⚠️  Plus 스캔 오류: {e}")

        # 5. DB 저장 — 기본 + Plus 한 세션으로 통합 저장
        all_results = results + plus_results
        await asyncio.to_thread(database.save_scan, all_results, len(tickers))

        # 6. 워치리스트 업데이트
        if results:
            await asyncio.to_thread(update_from_scan, results)

        # 7. 결과 전송
        if not all_results:
            await msg.edit_text(f"✅ 스캔 완료 ({len(tickers)}개)\n📭 감지된 패턴 없음")
            return

        extra_note = f" / +{len(plus_results)}" if plus_results else ""
        await msg.edit_text(
            f"✅ 스캔 완료!\n"
            f"📊 {len(tickers)}개 중 {len(results)}개 패턴 감지{extra_note}"
        )

        for r in all_results:
            try:
                await app.bot.send_message(
                    chat_id=USER_ID,
                    text=format_result(r),
                )
                await asyncio.sleep(0.5)
            except Exception:
                plain = (
                    f"{r['pattern']} 감지: {r['name']} ({r['ticker']})\n"
                    f"현재가: {r['current']:,}원 / 신뢰도: {r['conf']}%"
                )
                await app.bot.send_message(chat_id=USER_ID, text=plain)

    except Exception as e:
        await msg.edit_text(f"❌ 스캔 오류: {e}")


# ── 텔레그램 핸들러 ───────────────────────────────────────────────────

@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📈 *주식 패턴 알림 봇*\n\n"
        "명령어:\n"
        "/scan \\- 즉시 패턴 스캔\n"
        "/watchlist \\- 감지된 종목 목록\n"
        "/dashboard \\- 웹 대시보드 링크\n"
        "/model \\- 응답 모델 확인/변경 \\(claude \\| ollama\\)\n"
        "/add \\<종목코드\\> \\- 종목 수동 추가\n"
        "/remove \\<종목코드\\> \\- 종목 제거\n\n"
        "또는 *지금 스캔해줘* 라고 입력하세요\\.",
        parse_mode="MarkdownV2",
    )


@authorized
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await do_scan(context.application, is_manual=True)


@authorized
async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = get_all()
    stocks = data.get("stocks", [])

    if not stocks:
        await update.message.reply_text("워치리스트가 비어있습니다.")
        return

    last_scan = data.get("last_scan", "없음")
    lines = [f"📋 *워치리스트* \\(마지막 스캔: {last_scan}\\)\n"]
    for s in stocks:
        emoji = {"골삼이": "🔵", "골든샘플": "🟢", "레드삼각": "🔴", "골삼이(상승초입)": "🚀"}.get(s["pattern"], "📌")
        lines.append(
            f"{emoji} {s['name']} \\({s['ticker']}\\) \\- {s['pattern']} \\[{s['added_date']}\\]"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


@authorized
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("사용법: /add <종목코드>\n예) /add 005930")
        return

    ticker = context.args[0].strip()
    name = get_stock_name(ticker)

    if add_stock(ticker, name):
        await update.message.reply_text(f"✅ {name} ({ticker}) 추가됨")
    else:
        await update.message.reply_text(f"이미 워치리스트에 있습니다: {name} ({ticker})")


@authorized
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("사용법: /remove <종목코드>\n예) /remove 005930")
        return

    ticker = context.args[0].strip()
    if remove_stock(ticker):
        await update.message.reply_text(f"🗑 {ticker} 제거됨")
    else:
        await update.message.reply_text(f"워치리스트에 없는 종목: {ticker}")


@authorized
async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = context.application.bot_data.get("dashboard_url")
    if url:
        await update.message.reply_text(f"📊 대시보드 링크:\n{url}")
    else:
        await update.message.reply_text(
            "❌ ngrok이 실행되지 않았습니다.\n"
            ".env에 NGROK_AUTH_TOKEN을 추가하고 봇을 재시작하세요."
        )



async def _run_ollama(prompt: str) -> str:
    import ollama
    def _run():
        try:
            resp = ollama.chat(model=OLLAMA_MODEL, messages=[{"role": "user", "content": prompt}])
            try:
                return resp.message.content
            except AttributeError:
                return resp["message"]["content"]
        except Exception as e:
            return f"❌ Ollama 오류: {e}"
    return await asyncio.to_thread(_run)


async def _run_claude(prompt: str, cwd: str) -> str:
    def _run():
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--output-format", "text", "--dangerously-skip-permissions"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        out = result.stdout.strip()
        if result.returncode != 0 and result.stderr.strip():
            out += f"\n\n⚠️ stderr:\n{result.stderr.strip()}"
        return out or "(응답 없음)"
    return await asyncio.to_thread(_run)


async def _send_chunked(update: Update, text: str) -> None:
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])


def _get_model_info() -> str:
    lines = []
    active = f"{'✅' if _model == 'claude' else '⬜'} Claude CLI\n"
    active += f"{'✅' if _model == 'ollama' else '⬜'} Ollama\n"
    lines.append(f"🤖 현재 모델: {_model.upper()}\n")

    # Claude 정보
    lines.append("─── Claude CLI ───")
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "--version"],
            capture_output=True, text=True, timeout=5,
            env={k: v for k, v in os.environ.items() if k != "CLAUDECODE"},
        )
        ver = (result.stdout + result.stderr).strip().split("\n")[0]
        lines.append(f"버전: {ver}")
    except Exception as e:
        lines.append(f"버전: 확인 불가 ({e})")
    lines.append("분석모델: claude-haiku-4-5 (지표분석)")
    lines.append("")

    # Ollama 정보
    lines.append("─── Ollama ───")
    try:
        import ollama as _ol
        info = _ol.show(OLLAMA_MODEL)
        d = info.details
        mi = info.modelinfo or {}
        quant = d.quantization_level if d else "?"
        family = d.family if d else "?"
        param = d.parameter_size if d else "?"
        ctx = mi.get("qwen2.context_length") or mi.get(next((k for k in mi if "context_length" in k), ""), "?")
        lines.append(f"모델: {OLLAMA_MODEL}")
        lines.append(f"패밀리: {family}")
        lines.append(f"파라미터: {param}")
        lines.append(f"양자화: {quant}")
        lines.append(f"컨텍스트: {ctx:,}토큰" if isinstance(ctx, int) else f"컨텍스트: {ctx}")
        # 메모리 추정 (Q4_K_M ≈ 4.5bit/param)
        param_count = mi.get("general.parameter_count", 0)
        if param_count:
            mem_gb = param_count * 4.5 / 8 / 1e9
            lines.append(f"예상 메모리: ~{mem_gb:.1f}GB")
    except Exception as e:
        lines.append(f"정보 확인 불가: {e}")

    lines.append("")
    lines.append("변경: /model claude | /model ollama")
    return "\n".join(lines)


@authorized
async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _model
    if not context.args:
        info = await asyncio.to_thread(_get_model_info)
        await update.message.reply_text(info)
        return
    choice = context.args[0].lower()
    if choice == "claude":
        _model = "claude"
        await update.message.reply_text("✅ 모델 변경: Claude CLI")
    elif choice == "ollama":
        _model = "ollama"
        await update.message.reply_text(f"✅ 모델 변경: Ollama ({OLLAMA_MODEL})")
    else:
        await update.message.reply_text("❌ 알 수 없는 모델. claude 또는 ollama 중 선택하세요.")


@authorized
async def cmd_pwd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rel = os.path.relpath(_cwd, _ROOT)
    await update.message.reply_text(f"📁 {rel} ({_cwd})")


@authorized
async def cmd_cd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _cwd
    if not context.args:
        await update.message.reply_text("사용법: /cd <경로>\n예) /cd AI-stockAlarm")
        return
    new_dir = os.path.normpath(os.path.join(_cwd, context.args[0]))
    if not new_dir.startswith(_ROOT):
        await update.message.reply_text(f"❌ 루트 밖으로 이동 불가\n루트: {_ROOT}")
        return
    if not os.path.isdir(new_dir):
        await update.message.reply_text(f"❌ 디렉토리 없음: {new_dir}")
        return
    _cwd = new_dir
    await update.message.reply_text(f"📁 이동됨: {os.path.relpath(_cwd, _ROOT)}/")


@authorized
async def cmd_ls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        entries = sorted(os.listdir(_cwd))
        lines = [("📁 " if os.path.isdir(os.path.join(_cwd, e)) else "📄 ") + e for e in entries]
        rel = os.path.relpath(_cwd, _ROOT)
        await _send_chunked(update, f"📂 {rel}/\n\n" + "\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


@authorized
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    if "스캔" in text or "scan" in text.lower():
        await do_scan(context.application, is_manual=True)
        return

    # 그 외 텍스트 → 선택된 모델로 실행
    rel = os.path.relpath(_cwd, _ROOT)
    model_label = f"Claude (📁 {rel})" if _model == "claude" else f"Ollama ({OLLAMA_MODEL})"
    msg = await update.message.reply_text(f"⏳ {model_label} 처리 중...")
    try:
        if _model == "ollama":
            result = await _run_ollama(text)
        else:
            result = await _run_claude(text, _cwd)
        await msg.delete()
        await _send_chunked(update, _model_prefix() + result)
    except subprocess.TimeoutExpired:
        await msg.edit_text("❌ 타임아웃 (300초 초과)")
    except Exception as e:
        await msg.edit_text(f"❌ 오류: {e}")


# ── 스케줄러 설정 ─────────────────────────────────────────────────────

async def _scheduled_scan(app: Application) -> None:
    await do_scan(app, is_manual=False)


async def _scheduled_indicators(app: Application, time_slot: str = "morning") -> None:
    """지표 자동 수집"""
    import fetch_indicators as fi
    from datetime import datetime as _dt
    import database as _db

    today = _dt.now().strftime("%Y-%m-%d")
    history = _db.get_daily_indicators(3)  # 오늘 + 전일 비교용
    existing = history[0]["data"] if history and history[0]["date"] == today else {}
    prev_data = next((h["data"] for h in history if h["date"] != today), {})
    prev_score = next((h["crash_score"] for h in history if h["date"] != today), None)

    try:
        data = await asyncio.to_thread(fi.fetch_all, os.getenv("FRED_API_KEY",""), CLAUDE_KEY, existing)
    except Exception as e:
        print(f"⚠️ 지표 수집 오류: {e}")
        return

    STATUS_SCORE = {"위험":-2,"경고":-1,"관망":0,"긍정":1,"최상":2}
    WEIGHTS = {
        "usd_krw":             15,
        "us10y":               10,
        "foreign_flow":        15,
        "wti":                 12,
        "soxx":                8,
        "hy_spread":           20,
        "mmf_total":           8,
        "rrp":                 5,
        "tga":                 8,
        "yield_curve":         10,
        "fear_greed":          10,
        "btc":                 8,
        "vix":                 12,
        "nasdaq":              8,
        "gold":                5,
    }
    wsum, wmax = 0, 0
    for k, w in WEIGHTS.items():
        st = data.get(k, {}).get("status")
        if st and st in STATUS_SCORE:
            wsum += STATUS_SCORE[st] * w
        wmax += w * 2
    crash_score = round(50 - (wsum / wmax * 50) if wmax else 50, 1)
    _db.save_daily_indicators(today, data, crash_score, "자동 수집", time_slot)
    print(f"✅ 지표 수집 완료 [{time_slot}] (폭락스코어: {crash_score})")

    # CMS 스코어 함께 수집
    try:
        import cms_fetcher as cf
        fred_key = os.getenv("FRED_API_KEY", "")
        av_key   = os.getenv("ALPHA_VANTAGE_API_KEY", "")
        if fred_key:
            cms_result = await asyncio.to_thread(cf.fetch_cms, fred_key, av_key or None)
            _db.save_cms_snapshot(today, time_slot, cms_result["cms_score"],
                                  cms_result["regime"], cms_result)
            print(f"✅ CMS 수집 완료 [{time_slot}] (score: {cms_result['cms_score']} / {cms_result['regime']})")
    except Exception as e:
        print(f"⚠️  CMS 수집 오류: {e}")


async def _scheduled_future_indicators(app: Application) -> None:
    """미래방향성 지표 수집 — 매일 04:30 (API 패킷 리필 후)"""
    import future_indicators as fi
    import database as _db
    from datetime import datetime as _dt

    today = _dt.now().strftime("%Y-%m-%d")
    print(f"🧭 미래지표 수집 시작: {today}")
    try:
        data = await asyncio.to_thread(fi.fetch_future_indicators)
        _db.save_future_indicators(today, data)

        # 전일 스냅샷과 변경점 계산
        history = _db.get_future_indicators(2)
        changes = []
        if len(history) >= 2:
            prev = history[-2]["data"] if len(history) >= 2 else {}
            changes = fi.compute_changes(data, prev)

        # 텔레그램 요약 메시지
        scores = {k: round(v.get("score", 50)) for k, v in data.items()}
        avg_score = round(sum(scores.values()) / len(scores))

        lines = [f"🧭 *미래방향성 지표 업데이트* ({today})\n"]
        lines.append(f"종합 모멘텀: *{avg_score}/100*\n")

        if changes:
            lines.append("📊 주요 변동:")
            for c in changes[:4]:
                arrow = "↑" if c["direction"] == "up" else "↓"
                lines.append(f"  {c['icon']} {c['name']}: {arrow}{abs(c['delta']):.1f}pt")

        # 상위 3개 방향
        top3 = sorted(data.items(), key=lambda x: x[1].get("score", 50), reverse=True)[:3]
        lines.append("\n🚀 모멘텀 상위:")
        for k, v in top3:
            lines.append(f"  {v['icon']} {v['name']}: {v.get('score', 50):.0f}pt ({v.get('label', '')})")

        await app.bot.send_message(
            chat_id=USER_ID,
            text="\n".join(lines),
            parse_mode="Markdown",
        )
        print(f"✅ 미래지표 수집 완료 (종합: {avg_score})")
    except Exception as e:
        print(f"⚠️ 미래지표 수집 오류: {e}")
        try:
            await app.bot.send_message(chat_id=USER_ID, text=f"⚠️ 미래지표 수집 오류: {e}")
        except Exception:
            pass


async def _scheduled_lotto_notify(app: Application) -> None:
    """로또 구매 알림 — 매주 금요일 12:00. 추천번호를 DB에 저장."""
    import lotto as _lotto
    import database as _db
    try:
        draws = await asyncio.to_thread(_lotto.get_all_draws)
        if not draws:
            await asyncio.to_thread(_lotto.fetch_and_store)
            draws = await asyncio.to_thread(_lotto.get_all_draws)
        games = _lotto.get_recommendations(draws)
        latest = draws[-1]
        next_draw_no = latest['draw_no'] + 1   # 이번 주 추첨 회차

        # 추천번호 DB 저장 (토요일 비교용)
        await asyncio.to_thread(_db.save_lotto_recommendations, next_draw_no, games)

        ball_str = lambda nums: "  ".join(str(n) for n in nums)
        lines = ["🎱 *로또 안샀으면 사세요!*\n"]
        lines.append(f"📌 최근 당첨번호 (제{latest['draw_no']}회 {latest['date']})")
        lines.append(f"  {ball_str(latest['numbers'])}  +보너스 {latest['bonus']}\n")
        lines.append(f"🎯 *제{next_draw_no}회 추천 번호 (5게임)*")
        for g in games:
            lines.append(f"  [{g['label']}] {ball_str(g['numbers'])}")
        lines.append("\n🍀 행운을 빕니다!")

        await app.bot.send_message(
            chat_id=USER_ID,
            text="\n".join(lines),
            parse_mode="Markdown",
        )
        print(f"🎱 로또 알림 발송 완료 (제{next_draw_no}회 추천 저장)")
    except Exception as e:
        print(f"⚠️ 로또 알림 오류: {e}")


async def _scheduled_lotto_update(app: Application) -> None:
    """로또 당첨번호 DB 업데이트 + 지난회차 추천 비교 — 매주 토요일 21:00"""
    import lotto as _lotto
    import database as _db
    try:
        result = await asyncio.to_thread(_lotto.fetch_and_store)
        draws = await asyncio.to_thread(_lotto.get_all_draws)
        if not draws:
            print("🎱 로또 DB: 데이터 없음")
            return

        latest = draws[-1]
        ball_str = lambda nums: "  ".join(str(n) for n in nums)
        lines = []

        if result['saved'] > 0:
            lines.append(f"🎱 *제{latest['draw_no']}회 당첨번호*")
            lines.append(f"  {ball_str(latest['numbers'])}  +보너스 {latest['bonus']}")
            lines.append(f"  추첨일: {latest['date']}")
        else:
            lines.append(f"🎱 *제{latest['draw_no']}회* (이미 최신)")
            lines.append(f"  {ball_str(latest['numbers'])}  +보너스 {latest['bonus']}")

        # ── 지난 주 추천번호 비교 ──────────────────────────────────
        prev_games = await asyncio.to_thread(_db.get_lotto_recommendation, latest['draw_no'])
        if prev_games:
            win_set = set(latest['numbers'])
            bonus   = latest['bonus']
            lines.append(f"\n📊 *지난 추천번호 vs 실제 당첨 비교*")
            best_match = 0
            for g in prev_games:
                recs   = g['numbers']
                matched = [n for n in recs if n in win_set]
                has_bonus = bonus in recs
                cnt = len(matched)
                if cnt > best_match:
                    best_match = cnt
                # 등수 판정
                if cnt == 6:
                    rank = "🥇 1등!"
                elif cnt == 5 and has_bonus:
                    rank = "🥈 2등!"
                elif cnt == 5:
                    rank = "🥉 3등"
                elif cnt == 4:
                    rank = "4등"
                elif cnt == 3:
                    rank = "5등"
                else:
                    rank = f"{cnt}개 일치"
                matched_str = " ".join(f"*{n}*" if n in win_set else str(n) for n in recs)
                lines.append(f"  [{g['label']}] {matched_str} → {rank}")
            lines.append(f"\n  최고 일치: {best_match}개")
        else:
            lines.append("\n_(지난 추천번호 미저장 — 다음 주부터 비교 가능)_")

        await app.bot.send_message(
            chat_id=USER_ID,
            text="\n".join(lines),
            parse_mode="Markdown",
        )
        print(f"🎱 로또 업데이트 완료: 제{latest['draw_no']}회 (신규 {result['saved']}개)")
    except Exception as e:
        print(f"⚠️ 로또 DB 업데이트 오류: {e}")


async def _scheduled_war_indicators(app: Application) -> None:
    """전쟁지표 수집 — 매일 04:35"""
    import war_indicators as wi
    import database as _db
    from datetime import datetime as _dt

    today = _dt.now().strftime("%Y-%m-%d")
    print(f"⚔️  전쟁지표 수집 시작: {today}")
    existing = _db.get_war_indicators_latest()
    ex_data  = existing["data"] if existing else {}
    try:
        data = await asyncio.to_thread(wi.fetch_all_war, CLAUDE_KEY, ex_data)
        _db.save_war_indicators(today, data)
        attacks = data.get("attacks", {})
        total7  = attacks.get("total_7d", {}).get("total", 0)
        status  = attacks.get("status", "관망")
        await app.bot.send_message(
            chat_id=USER_ID,
            text=f"⚔️ *전쟁지표 업데이트* ({today})\n공격 관련 기사 7일: {total7}건 ({status})",
            parse_mode="Markdown",
        )
        print(f"✅ 전쟁지표 수집 완료")
    except Exception as e:
        print(f"⚠️  전쟁지표 수집 오류: {e}")


async def _scheduled_calendar_refresh(app: Application) -> None:
    """경제 캘린더 캐시 갱신 — 매일 05:00"""
    import calendar_fetcher as cf
    year = __import__('datetime').datetime.now().year
    print(f"📅 경제 캘린더 갱신: {year}년")
    try:
        cf._YEAR_CACHE.pop(year, None)
        cf._MAIN_CACHE["ts"] = 0
        await asyncio.to_thread(cf.fetch_calendar_year, year)
        print("✅ 경제 캘린더 갱신 완료")
    except Exception as e:
        print(f"⚠️  경제 캘린더 갱신 오류: {e}")


async def _scheduled_news_collect(app: Application) -> None:
    """뉴스 수집 + Claude 분석 — 매일 06:30"""
    import news_collector as nc
    import database as _db
    from datetime import datetime as _dt
    today = _dt.now().strftime("%Y-%m-%d")
    print(f"📰 뉴스 수집 시작: {today}")
    try:
        result   = await asyncio.to_thread(nc.fetch_and_analyze, CLAUDE_KEY, 1)
        articles = result.get("articles", [])
        analysis = result.get("analysis", {})
        saved    = _db.save_news_articles(articles)
        if analysis:
            _db.save_news_analysis(today, analysis)
        msg = (f"📰 *뉴스 수집 완료* ({today})\n"
               f"수집: {len(articles)}건 / 저장: {saved}건\n"
               f"감성점수: {analysis.get('sentiment_score','?')}")
        await app.bot.send_message(chat_id=USER_ID, text=msg, parse_mode="Markdown")
        print(f"✅ 뉴스 수집 완료: {saved}건")
    except Exception as e:
        print(f"⚠️  뉴스 수집 오류: {e}")


async def _scheduled_month_backfill(app: Application) -> None:
    """월말 역사 뉴스 백필 — 매월 마지막 날 23:00 (토큰 리셋 직전 최대 수집)"""
    import news_backfill as nb
    print("📚 월말 역사 백필 시작...")
    try:
        result = await asyncio.to_thread(nb.run_backfill, '2020-01', 20, CLAUDE_KEY)
        msg = (f"📚 *월말 뉴스 백필 완료*\n"
               f"처리: {result['processed_weeks']}주 / "
               f"기사: {result['total_articles']}건 / "
               f"남은 주: {result['remaining_weeks']}주")
        await app.bot.send_message(chat_id=USER_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"⚠️  월말 백필 오류: {e}")


async def _scheduled_crawl(app: Application) -> None:
    """장 마감 후 당일 OHLCV + 지표 수집 (16:00)"""
    from datetime import datetime as _dt
    import crawl_daily
    today = _dt.now().strftime("%Y%m%d")
    print(f"📡 일일 크롤 시작: {today}")
    try:
        await asyncio.to_thread(crawl_daily.run_daily, today)
        print("✅ 일일 크롤 완료")
    except Exception as e:
        print(f"⚠️  일일 크롤 오류: {e}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """전역 에러 핸들러 — Conflict는 무시하고 나머지만 로그"""
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        return  # 다른 인스턴스 충돌은 조용히 무시
    print(f"⚠️ 봇 에러: {context.error}")


def _start_web_server() -> None:
    import uvicorn
    uvicorn.run(web_app, host="0.0.0.0", port=8080, log_level="warning")


async def post_init(app: Application) -> None:
    # DB 초기화
    database.init_db()

    # 웹 서버 — 데몬 스레드로 시작 (봇 종료 시 자동 종료)
    web_thread = threading.Thread(target=_start_web_server, daemon=True, name="web-server")
    web_thread.start()
    print("🌐 웹 대시보드: http://0.0.0.0:8080")

    # ngrok 터널
    if NGROK_TOKEN:
        try:
            from pyngrok import ngrok, conf
            conf.get_default().auth_token = NGROK_TOKEN

            tunnel = ngrok.connect(8080, "http")
            dashboard_url = tunnel.public_url
            app.bot_data["dashboard_url"] = dashboard_url
            print(f"🔗 ngrok 대시보드: {dashboard_url}")

            await app.bot.send_message(
                    chat_id=USER_ID,
                    text=f"📊 대시보드가 열렸습니다:\n{dashboard_url}",
                )
        except Exception as e:
            print(f"⚠️ ngrok 시작 실패: {e}")
    else:
        print("ℹ️ NGROK_AUTH_TOKEN 없음 — ngrok 비활성화")

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # ① 장 마감 후 스캔: 평일 15:40
    scheduler.add_job(
        _scheduled_scan,
        "cron",
        day_of_week="mon-fri",
        hour=15,
        minute=40,
        args=[app],
    )

    # ④ 지표 자동 수집 — 매일 4회 (주말 포함)
    for _hour, _minute, _slot in [
        (8,  10, "morning"),    # 08:10 아침 (미국 전일 마감 후)
        (16, 0,  "afternoon"),  # 16:00 오후 (한국장 마감 후)
        (22, 0,  "night"),      # 22:00 밤
        (4,  0,  "dawn"),       # 04:00 새벽 (미국장 중간)
    ]:
        scheduler.add_job(
            _scheduled_indicators,
            "cron",
            hour=_hour,
            minute=_minute,
            args=[app, _slot],
        )

    # ⑤ 미래방향성 지표: 매일 04:30 (API 패킷 리필 후)
    scheduler.add_job(
        _scheduled_future_indicators,
        "cron",
        hour=4,
        minute=30,
        args=[app],
    )

    # ⑦ 뉴스 수집 + 분석: 매일 06:30
    scheduler.add_job(
        _scheduled_news_collect,
        "cron",
        hour=6,
        minute=30,
        args=[app],
    )

    # ⑧ 월말 역사 백필: 매월 마지막 날 23:00
    scheduler.add_job(
        _scheduled_month_backfill,
        "cron",
        day="last",
        hour=23,
        minute=0,
        args=[app],
    )

    # ⑥ 전쟁지표: 매일 04:35
    scheduler.add_job(
        _scheduled_war_indicators,
        "cron",
        hour=4,
        minute=35,
        args=[app],
    )

    # ⑪ 경제 캘린더 캐시 갱신: 매일 05:00
    scheduler.add_job(
        _scheduled_calendar_refresh,
        "cron",
        hour=5,
        minute=0,
        args=[app],
    )

    # ⑨ 로또 구매 알림: 매주 금요일 12:00
    scheduler.add_job(
        _scheduled_lotto_notify,
        "cron",
        day_of_week="fri",
        hour=12,
        minute=0,
        args=[app],
    )

    # ⑩ 로또 당첨번호 업데이트: 매주 토요일 21:00 (추첨 후)
    scheduler.add_job(
        _scheduled_lotto_update,
        "cron",
        day_of_week="sat",
        hour=21,
        minute=0,
        args=[app],
    )

    # ③ 장 마감 후 일일 크롤: 평일 16:00
    scheduler.add_job(
        _scheduled_crawl,
        "cron",
        day_of_week="mon-fri",
        hour=16,
        minute=0,
        args=[app],
    )

    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    print("⏰ 스케줄러 시작: 스캔 15:40 / 뉴스 08:00 / 크롤 16:00 (평일)")


async def post_shutdown(app: Application) -> None:
    scheduler = app.bot_data.get("scheduler")
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)


# ── 진입점 ────────────────────────────────────────────────────────────

def main() -> None:
    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN이 .env에 없습니다.")
        return
    if not USER_ID:
        print("❌ TELEGRAM_USER_ID가 .env에 없습니다.")
        return

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("pwd", cmd_pwd))
    app.add_handler(CommandHandler("cd", cmd_cd))
    app.add_handler(CommandHandler("ls", cmd_ls))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("📈 주식 알림 봇 시작!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
