"""
main.py
한국주식 패턴 알림 텔레그램 봇
- ① 15:40 자동 스캔 (KOSPI+KOSDAQ 중소형주)
- ② 08:00 자동 뉴스 분석 (워치리스트 종목)
- ③ 수동: /scan, /news, "지금 스캔해줘"
"""

import asyncio
import os
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

# AI-stockAlarm 디렉토리를 Python 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher import get_candidates_cached, get_stock_name
from scanner import scan_all, format_result
from news_analyzer import analyze_watchlist, format_news_result
from watchlist import update_from_scan, add_stock, remove_stock, get_all
from sector_info import enrich_results
import database
from web_server import app as web_app

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
USER_ID = int(os.getenv("TELEGRAM_USER_ID", "0"))
CLAUDE_KEY = os.getenv("CLAUDE_API_KEY")
DART_KEY = os.getenv("DART_API_KEY", "")
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

        # 2. 패턴 탐지
        results: list[dict] = await asyncio.to_thread(scan_all, tickers)

        # 3. 업종/테마 보강 — AI 자원 절약을 위해 비활성화
        # if results:
        #     await msg.edit_text(f"✅ {len(results)}개 패턴 감지! 업종/테마 분석 중...")
        #     results = await asyncio.to_thread(enrich_results, results, claude_client)

        # 4. DB 저장
        await asyncio.to_thread(database.save_scan, results, len(tickers))

        # 5. 워치리스트 업데이트
        if results:
            await asyncio.to_thread(update_from_scan, results)

        # 6. 결과 전송
        if not results:
            await msg.edit_text(f"✅ 스캔 완료 ({len(tickers)}개)\n📭 감지된 패턴 없음")
            return

        await msg.edit_text(f"✅ 스캔 완료!\n📊 {len(tickers)}개 중 {len(results)}개 패턴 감지")

        for r in results:
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


async def do_news(app: Application) -> None:
    """워치리스트 뉴스 분석 (② 자동 / 수동 공용)"""
    data = get_all()
    stocks = data.get("stocks", [])

    if not stocks:
        await app.bot.send_message(
            chat_id=USER_ID,
            text="📰 워치리스트가 비어있습니다.\n먼저 /scan 을 실행하세요.",
        )
        return

    msg = await app.bot.send_message(
        chat_id=USER_ID,
        text=f"📰 뉴스 분석 시작... ({len(stocks)}개 종목)",
    )

    try:
        results = await asyncio.to_thread(
            analyze_watchlist, stocks, DART_KEY, claude_client
        )

        if not results:
            await msg.edit_text("📰 관련 뉴스/공시 없음")
            return

        from datetime import datetime
        header = f"📰 *뉴스 분석* \\({datetime.now().strftime('%m/%d %H:%M')}\\)\n{'━' * 18}"
        await msg.edit_text(header, parse_mode="MarkdownV2")

        for r in results:
            try:
                await app.bot.send_message(
                    chat_id=USER_ID,
                    text=format_news_result(r),
                    parse_mode="MarkdownV2",
                )
                await asyncio.sleep(0.5)
            except Exception:
                plain = f"{r['name']} ({r['ticker']})\n{r.get('analysis', '')}"
                await app.bot.send_message(chat_id=USER_ID, text=plain)

    except Exception as e:
        await msg.edit_text(f"❌ 뉴스 분석 오류: {e}")


# ── 텔레그램 핸들러 ───────────────────────────────────────────────────

@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📈 *주식 패턴 알림 봇*\n\n"
        "명령어:\n"
        "/scan \\- 즉시 패턴 스캔\n"
        "/news \\- 워치리스트 뉴스 분석\n"
        "/watchlist \\- 감지된 종목 목록\n"
        "/dashboard \\- 웹 대시보드 링크\n"
        "/add \\<종목코드\\> \\- 종목 수동 추가\n"
        "/remove \\<종목코드\\> \\- 종목 제거\n\n"
        "또는 *지금 스캔해줘* 라고 입력하세요\\.",
        parse_mode="MarkdownV2",
    )


@authorized
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await do_scan(context.application, is_manual=True)


@authorized
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await do_news(context.application)


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



@authorized
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    if "스캔" in text or "scan" in text.lower():
        await do_scan(context.application, is_manual=True)


# ── 스케줄러 설정 ─────────────────────────────────────────────────────

async def _scheduled_scan(app: Application) -> None:
    await do_scan(app, is_manual=False)


async def _scheduled_news(app: Application) -> None:
    await do_news(app)


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

    # ② 아침 뉴스: 평일 08:00
    scheduler.add_job(
        _scheduled_news,
        "cron",
        day_of_week="mon-fri",
        hour=8,
        minute=0,
        args=[app],
    )

    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    print("⏰ 스케줄러 시작: 스캔 15:40 / 뉴스 08:00 (평일)")


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
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("📈 주식 알림 봇 시작!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
