import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


# ============================================================
# 設定
# ============================================================

PLAYLIST_URL = (
    "https://open.spotify.com/playlist/"
    "37i9dQZEVXbKXQ4mDTEBXq"
)

PLAYLIST_ID = "37i9dQZEVXbKXQ4mDTEBXq"
PLAYLIST_NAME = "Top 50 - Japan"

OUTPUT = Path("data/top50-japan.json")

TARGET_TRACKS = 50

# 最大スクロール回数
MAX_SCROLLS = 60

# スクロール後の待機時間
SCROLL_WAIT = 1.5

# 曲数が増えなくても追加で試す回数
MAX_STABLE_SCROLLS = 5

# Playwright設定
PAGE_TIMEOUT = 60_000

VIEWPORT_WIDTH = 1440
VIEWPORT_HEIGHT = 1200


# ============================================================
# ログ
# ============================================================

def log(message=""):
    print(message, flush=True)


# ============================================================
# 曲情報取得
# ============================================================

def extract_track(row):
    """
    Spotifyのtracklist-rowから曲情報を取得する。
    """

    try:
        # ----------------------------------------------------
        # 曲名・Spotify URL
        # ----------------------------------------------------

        title_element = row.locator(
            '[data-testid="internal-track-link"]'
        ).first

        if title_element.count() == 0:
            return None

        title = title_element.inner_text().strip()

        if not title:
            return None

        href = title_element.get_attribute("href")

        if not href:
            return None

        if href.startswith("/"):
            spotify_url = (
                f"https://open.spotify.com{href}"
            )
        else:
            spotify_url = href

        # URLにクエリが付いている場合は削除
        spotify_url = spotify_url.split("?")[0]

        # ----------------------------------------------------
        # アーティスト
        # ----------------------------------------------------

        artist_element = row.locator(
            'a[href*="/artist/"]'
        ).first

        if artist_element.count() > 0:
            artist = artist_element.inner_text().strip()
        else:
            artist = ""

        # ----------------------------------------------------
        # 再生回数
        # ----------------------------------------------------

        play_count = None

        play_count_element = row.locator(
            '[role="gridcell"][aria-colindex="3"]'
        ).first

        if play_count_element.count() > 0:

            text = (
                play_count_element
                .inner_text()
                .strip()
            )

            # 例:
            # 288,431
            # 288 431

            cleaned = re.sub(
                r"[,\s]",
                "",
                text,
            )

            if cleaned.isdigit():
                play_count = int(cleaned)

        # ----------------------------------------------------
        # 再生時間
        # ----------------------------------------------------

        duration = None

        duration_element = row.locator(
            '[role="gridcell"][aria-colindex="5"]'
        ).first

        if duration_element.count() > 0:

            duration_text = (
                duration_element
                .inner_text()
                .strip()
            )

            matches = re.findall(
                r"\b\d+:\d{2}\b",
                duration_text,
            )

            if matches:
                duration = matches[-1]

        # ----------------------------------------------------
        # 結果
        # ----------------------------------------------------

        return {
            "title": title,
            "artist": artist,
            "duration": duration,
            "play_count": play_count,
            "spotify_url": spotify_url,
        }

    except Exception as exc:

        log(
            f"  [WARN] 曲情報の取得に失敗: {exc}"
        )

        return None


# ============================================================
# スクロール
# ============================================================

def scroll_playlist(page):
    """
    Spotifyの仮想スクロール領域を下方向へ移動する。

    複数のスクロールコンテナを検出して処理する。
    """

    page.evaluate(
        """
        () => {

            const elements = [
                document.documentElement,
                document.body,
                ...document.querySelectorAll("*")
            ];

            const scrollables = elements.filter(el => {

                const style = getComputedStyle(el);

                return (
                    (
                        style.overflowY === "auto" ||
                        style.overflowY === "scroll"
                    )
                    &&
                    el.scrollHeight > el.clientHeight
                );

            });

            scrollables.forEach(el => {

                el.scrollTop += 2500;

            });

            window.scrollBy(0, 2500);
        }
        """
    )


# ============================================================
# 現在DOMに存在する曲を取得
# ============================================================

def collect_visible_tracks(page, tracks_by_url):
    """
    現在DOM上に存在するtracklist-rowを取得する。
    """

    rows = page.locator(
        '[data-testid="tracklist-row"]'
    )

    row_count = rows.count()

    new_tracks = 0

    for index in range(row_count):

        row = rows.nth(index)

        track = extract_track(row)

        if track is None:
            continue

        spotify_url = track["spotify_url"]

        if spotify_url in tracks_by_url:
            continue

        tracks_by_url[spotify_url] = track

        new_tracks += 1

        play_count = track["play_count"]

        if play_count is not None:
            play_count_text = f"{play_count:,}"
        else:
            play_count_text = "取得不可"

        log(
            f"取得 {len(tracks_by_url):2}曲: "
            f"{track['title']} / "
            f"{track['artist']} / "
            f"再生回数 {play_count_text}"
        )

        if len(tracks_by_url) >= TARGET_TRACKS:
            break

    return new_tracks


# ============================================================
# Spotifyスクレイピング
# ============================================================

def scrape_spotify():

    tracks_by_url = {}

    with sync_playwright() as playwright:

        log("Chromiumを起動しています...")

        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        try:

            page = browser.new_page(
                locale="ja-JP",
                viewport={
                    "width": VIEWPORT_WIDTH,
                    "height": VIEWPORT_HEIGHT,
                },
                user_agent=(
                    "Mozilla/5.0 "
                    "(X11; Linux x86_64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/131.0.0.0 "
                    "Safari/537.36"
                ),
            )

            page.set_default_timeout(
                PAGE_TIMEOUT
            )

            # ------------------------------------------------
            # Spotifyへアクセス
            # ------------------------------------------------

            log()
            log("Spotifyにアクセスしています...")

            response = page.goto(
                PLAYLIST_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )

            if response is not None:

                log(
                    f"HTTP Status: {response.status}"
                )

                if response.status >= 400:

                    raise RuntimeError(
                        "Spotifyページへのアクセスに失敗しました: "
                        f"HTTP {response.status}"
                    )

            # ------------------------------------------------
            # 曲一覧を待つ
            # ------------------------------------------------

            log(
                "曲一覧が表示されるまで待っています..."
            )

            page.wait_for_selector(
                '[data-testid="tracklist-row"]',
                timeout=PAGE_TIMEOUT,
            )

            log("曲一覧を確認しました。")
            log()

            # 初期読み込みを少し待つ
            page.wait_for_timeout(2000)

            # ------------------------------------------------
            # スクロールして取得
            # ------------------------------------------------

            stable_scrolls = 0

            for scroll_number in range(
                1,
                MAX_SCROLLS + 1,
            ):

                new_tracks = collect_visible_tracks(
                    page,
                    tracks_by_url,
                )

                # --------------------------------------------
                # 50曲取得
                # --------------------------------------------

                if len(tracks_by_url) >= TARGET_TRACKS:

                    log()
                    log("50曲取得しました！")
                    break

                # --------------------------------------------
                # 新しい曲が増えたか
                # --------------------------------------------

                if new_tracks == 0:
                    stable_scrolls += 1
                else:
                    stable_scrolls = 0

                log(
                    f"[スクロール "
                    f"{scroll_number}/{MAX_SCROLLS}] "
                    f"{len(tracks_by_url)}曲取得"
                )

                # --------------------------------------------
                # スクロール
                # --------------------------------------------

                scroll_playlist(page)

                page.wait_for_timeout(
                    int(SCROLL_WAIT * 1000)
                )

                # --------------------------------------------
                # 曲が増えない場合
                # --------------------------------------------

                if stable_scrolls >= MAX_STABLE_SCROLLS:

                    log(
                        "曲数が増えていないため、"
                        "追加で待機します..."
                    )

                    page.wait_for_timeout(3000)

                    # マウスホイールでもスクロール
                    page.mouse.move(
                        VIEWPORT_WIDTH // 2,
                        VIEWPORT_HEIGHT // 2,
                    )

                    page.mouse.wheel(
                        0,
                        4000,
                    )

                    page.wait_for_timeout(2500)

                    # 再度曲を確認
                    collect_visible_tracks(
                        page,
                        tracks_by_url,
                    )

                    if len(tracks_by_url) >= TARGET_TRACKS:
                        break

                    # それでも増えない場合
                    if stable_scrolls >= MAX_STABLE_SCROLLS + 2:

                        log(
                            "これ以上曲を取得できないため終了します。"
                        )

                        break

            else:

                log(
                    "最大スクロール回数に到達しました。"
                )

        finally:

            browser.close()

    return tracks_by_url


# ============================================================
# データ整形
# ============================================================

def build_result(tracks_by_url):

    total = len(tracks_by_url)

    log()
    log("=" * 50)
    log(f"ユニーク取得曲数: {total}")
    log("=" * 50)

    if total < TARGET_TRACKS:

        raise RuntimeError(
            f"{TARGET_TRACKS}曲取得できませんでした。"
            f"取得できたのは {total}曲です。"
        )

    # Python 3.7+ではdictの挿入順が保証されるため、
    # Spotify上で取得した順番を維持する。
    selected_tracks = list(
        tracks_by_url.values()
    )[:TARGET_TRACKS]

    tracks = []

    for rank, track in enumerate(
        selected_tracks,
        start=1,
    ):

        tracks.append(
            {
                "rank": rank,
                "title": track["title"],
                "artist": track["artist"],
                "duration": track["duration"],
                "play_count": track["play_count"],
                "spotify_url": track["spotify_url"],
            }
        )

    return {
        "playlist": PLAYLIST_NAME,
        "playlist_id": PLAYLIST_ID,
        "fetched_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "tracks": tracks,
    }


# ============================================================
# ターミナル表示
# ============================================================

def print_tracks(tracks):

    log()
    log("=" * 70)
    log("             Spotify Top 50 - Japan")
    log("=" * 70)

    for track in tracks:

        duration = (
            track["duration"]
            or "--:--"
        )

        play_count = track["play_count"]

        if play_count is not None:
            play_count_text = f"{play_count:,}"
        else:
            play_count_text = "取得不可"

        log(
            f"{track['rank']:2}. "
            f"{track['title']} - "
            f"{track['artist']} "
            f"[{duration}] "
            f"再生回数: {play_count_text}"
        )

    log("=" * 70)
    log(f"{len(tracks)}曲取得しました")


# ============================================================
# JSON保存
# ============================================================

def save_json(result):

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    json_text = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    )

    OUTPUT.write_text(
        json_text + "\n",
        encoding="utf-8",
    )

    log()
    log(
        f"JSON保存完了: {OUTPUT}"
    )


# ============================================================
# メイン
# ============================================================

def main():

    try:

        tracks_by_url = scrape_spotify()

        result = build_result(
            tracks_by_url
        )

        print_tracks(
            result["tracks"]
        )

        save_json(result)

        log()
        log("スクレイピング完了！")

    except PlaywrightTimeoutError as exc:

        log()
        log(
            "[ERROR] Playwright timeout"
        )
        log(str(exc))

        sys.exit(1)

    except Exception as exc:

        log()
        log(
            f"[ERROR] {exc}"
        )

        sys.exit(1)


if __name__ == "__main__":
    main()
