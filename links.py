def normalize_channel(s: str) -> str:
    s = s.strip()
    if s.startswith("https://t.me/"):
        s = s.replace("https://t.me/", "").strip("/")
    if s.startswith("@"):
        s = s[1:]
    return s

def channel_link(username_or_link: str) -> str:
    u = normalize_channel(username_or_link)
    return f"https://t.me/{u}"
