import json
import os
import smtplib
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.text import MIMEText

# 密钥和频道全部从环境变量读取（GitHub 上的加密变量），代码里不放任何密钥
API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
EMAIL = os.environ.get("QQ_EMAIL", "")
AUTH_CODE = os.environ.get("QQ_AUTH_CODE", "")
CHANNELS = [c.strip() for c in os.environ.get("CHANNELS", "").split(",") if c.strip()]

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
CN_TZ = timezone(timedelta(hours=8))


def get(url):
    last_error = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8")
        except Exception as e:
            last_error = e
            time.sleep(2 * (attempt + 1))
    raise last_error


def api(endpoint, **params):
    params["key"] = API_KEY
    url = "https://www.googleapis.com/youtube/v3/" + endpoint + "?" + urllib.parse.urlencode(params)
    return json.loads(get(url))


def resolve_channel(ref):
    if ref.startswith("UC"):
        data = api("channels", part="snippet", id=ref)
    else:
        data = api("channels", part="snippet", forHandle=ref.lstrip("@"))
    items = data.get("items", [])
    if not items:
        raise RuntimeError("找不到频道: " + ref)
    return items[0]["id"], items[0]["snippet"]["title"]


def rss_video_ids(channel_id):
    root = ET.fromstring(get("https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id))
    ids = []
    for e in root.findall(ATOM + "entry"):
        vid = e.findtext(YT + "videoId")
        if vid:
            ids.append(vid)
    return ids


def video_info(item):
    """返回 (状态, 预定开播时间文字)。状态：upcoming / live / ended / normal"""
    ld = item.get("liveStreamingDetails")
    if not ld:
        return "normal", ""
    if ld.get("actualEndTime"):
        return "ended", ""
    if ld.get("actualStartTime"):
        return "live", ""
    sched = ld.get("scheduledStartTime", "")
    text = ""
    if sched:
        dt = datetime.fromisoformat(sched.replace("Z", "+00:00"))
        text = dt.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M（北京时间）")
    return "upcoming", text


def send_email(subject, body):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = EMAIL
    msg["To"] = EMAIL
    with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=20) as s:
        s.login(EMAIL, AUTH_CODE)
        s.sendmail(EMAIL, [EMAIL], msg.as_string())


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("notified", []))
    except Exception:
        return set()


def save_state(notified):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"notified": sorted(notified)}, f, ensure_ascii=False, indent=2)


def main():
    if not (API_KEY and EMAIL and AUTH_CODE and CHANNELS):
        raise SystemExit("缺少环境变量：YOUTUBE_API_KEY / QQ_EMAIL / QQ_AUTH_CODE / CHANNELS")

    notified = load_state()
    sent = 0

    for ref in CHANNELS:
        try:
            cid, ch_title = resolve_channel(ref)
            ids = rss_video_ids(cid)
            if not ids:
                print(ch_title, ": RSS 里没有视频")
                continue
            data = api("videos", part="snippet,liveStreamingDetails", id=",".join(ids))
            items = {it["id"]: it for it in data.get("items", [])}
        except Exception as e:
            print("检查失败:", ref, e)
            continue

        print("频道:", ch_title, "| 视频数:", len(items))

        for vid in ids:
            item = items.get(vid)
            if not item:
                continue
            state, when = video_info(item)
            if state not in ("upcoming", "live"):
                continue
            key = vid + ":" + state + ":" + when
            if key in notified:
                continue
            title = item["snippet"]["title"]
            url = "https://www.youtube.com/watch?v=" + vid
            if state == "upcoming":
                subject = "【直播预告】" + ch_title
                body = title + "\n\n预定开播：" + when + "\n" + url
            else:
                subject = "【正在直播】" + ch_title
                body = title + "\n\n" + url
            try:
                send_email(subject, body)
                notified.add(key)
                sent += 1
                print("已通知:", subject, title)
            except Exception as e:
                print("发邮件失败:", e)

    save_state(notified)
    print("完成，本次发送", sent, "封邮件")


main()