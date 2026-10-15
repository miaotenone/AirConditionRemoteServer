#!/usr/bin/env python3
"""Edit the bot's custom menu (the 悬浮菜单 above the chat input) via OpenAPI.

The console is the usual way to do this, but the menu is also writable through

    PATCH /open-apis/application/v7/applications/:app_id/ability

whose body carries ``bot.enable`` / ``bot.bot_menu_enable`` /
``bot.bot_menu_display_strategy`` / ``bot.bot_menus[]``. It needs the app scope
``application:application:self_manage``.

**Read and write use different shapes**, which is the trap here:

* v7 write wants *nested* menus -- ``bot_menus`` is the list of **top-level**
  groups only (max 10 of them), each carrying ``children``. There is no
  ``menu_id`` / ``parent_menu_id`` / ``sort``; order is the array order. That
  is the ``BotMenuConfig`` / ``BotMenuConfig1`` pair. The SDK types this field
  as ``List[BotMenuNode]`` (flat, no ``children``), so we send raw JSON.
* v6 read gives a *flat* list with ``parent_menu_id``, via the app's versions:

      GET /open-apis/application/v6/applications/:app_id/app_versions

  whose ``ability.bot.bot_menus`` is the menu as of that version. There is no
  GET for /ability itself, so this is how the current menu is read back.

``menu_content_type``: 3 = 分组容器 (no action), 4 = 发送文字消息 (the display
name *is* the text sent). So every name below must be a sentence the server's
parser understands -- see docs/feishu-setup.md section 13, the source of truth.

    python scripts/feishu_menu.py            # show the diff (dry run)
    python scripts/feishu_menu.py --show     # dump the live menu as JSON
    python scripts/feishu_menu.py --apply    # actually write it
"""
import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

import lark_oapi as lark  # noqa: E402
from lark_oapi.api.application.v6 import (  # noqa: E402
    GetApplicationRequest,
    ListApplicationAppVersionRequest,
)

API = "https://open.feishu.cn/open-apis"
CT_CONTAINER = 3  # 分组容器: carries no action
CT_TEXT = 4  # 发送文字消息: the display name is the text

#: The menu we want, mirroring docs/feishu-setup.md section 13.
#: (group, [items, in order])
MENU = [
    ("卡片", ["设置卡片", "状态"]),
    ("模式", ["自动", "制冷", "除湿", "送风", "制热"]),
    ("风速", ["自动风", "低风", "中风", "最大风"]),
    ("开关", ["开机", "关机"]),
    ("其他", ["帮助", "预设", "夜间", "天气", "历史"]),
]


def app_id():
    return os.environ["FEISHU_APP_ID"]


def client():
    return (
        lark.Client.builder()
        .app_id(app_id())
        .app_secret(os.environ["FEISHU_APP_SECRET"])
        .log_level(lark.LogLevel.WARNING)
        .build()
    )


def tenant_token():
    resp = requests.post(
        f"{API}/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id(), "app_secret": os.environ["FEISHU_APP_SECRET"]},
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        sys.exit(f"cannot get a tenant token: {data.get('code')} {data.get('msg')}")
    return data["tenant_access_token"]


def read_state(cli):
    """(app, version_label, flat menu nodes, bot dict) for the online version."""
    app_resp = cli.application.v6.application.get(
        GetApplicationRequest.builder().app_id(app_id()).lang("zh_cn").build()
    )
    if app_resp.code != 0:
        sys.exit(f"cannot read the app: {app_resp.code} {app_resp.msg}")
    app = json.loads(app_resp.raw.content.decode("utf-8"))["data"]["app"]

    ver_resp = cli.application.v6.application_app_version.list(
        ListApplicationAppVersionRequest.builder()
        .app_id(app_id())
        .lang("zh_cn")
        .page_size(20)
        .build()
    )
    if ver_resp.code != 0:
        sys.exit(f"cannot list app versions: {ver_resp.code} {ver_resp.msg}")
    versions = json.loads(ver_resp.raw.content.decode("utf-8"))["data"]["items"]

    chosen = next(
        (v for v in versions if v.get("version_id") == app.get("online_version_id")),
        versions[0] if versions else None,
    )
    if chosen is None:
        sys.exit("the app has no versions to read the menu from")
    bot = (chosen.get("ability") or {}).get("bot") or {}
    return app, chosen.get("version"), bot.get("bot_menus") or [], bot


def index(live):
    """Flat read -> containers by name, and (group, item) -> node."""
    names = {n.get("menu_id"): n.get("default_name") for n in live}
    containers, items = {}, {}
    for n in live:
        parent = n.get("parent_menu_id")
        if parent:
            items[(names.get(parent), n.get("default_name"))] = n
        else:
            containers[n.get("default_name")] = n
    return containers, items


def flatten_read(live):
    """Flat read -> {group: [items]} so it can be compared with MENU."""
    names = {n.get("menu_id"): n.get("default_name") for n in live}
    out = {}
    for n in live:
        if n.get("parent_menu_id"):
            out.setdefault(names.get(n.get("parent_menu_id")), []).append(
                n.get("default_name")
            )
    return out


def nested_menu():
    """The v7 write shape: top-level groups, children nested, no ids."""
    return [
        {
            "default_name": group,
            "menu_content_type": CT_CONTAINER,
            "children": [
                {"default_name": item, "menu_content_type": CT_TEXT} for item in items
            ],
        }
        for group, items in MENU
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the menu (default: dry run)")
    ap.add_argument("--show", action="store_true", help="dump the live menu and exit")
    args = ap.parse_args()

    cli = client()
    app, version, live, bot = read_state(cli)

    groups = len({n.get("parent_menu_id") for n in live if n.get("parent_menu_id")})
    items = sum(1 for n in live if n.get("parent_menu_id"))
    print(f"live menu: version {version}, {groups} groups / {items} items "
          f"({len(live)} nodes), enable={bot.get('bot_menu_enable')}, "
          f"strategy={bot.get('bot_menu_display_strategy')}, "
          f"online_version_id={app.get('online_version_id')}")

    if args.show:
        print(json.dumps(live, ensure_ascii=False, indent=2))
        return 0

    have = flatten_read(live)
    want = {group: list(items_) for group, items_ in MENU}

    added = [(g, i) for g, its in want.items() for i in its if i not in have.get(g, [])]
    removed = [(g, i) for g, its in have.items() for i in its if i not in want.get(g, [])]
    missing_groups = [g for g in want if g not in have]

    print(f"desired menu: {len(MENU)} groups / {sum(len(i) for i in want.values())} items")
    for g, i in added:
        print(f"  + {g} / {i}")
    for g, i in removed:
        print(f"  - {g} / {i}")
    for g in missing_groups:
        print(f"  + group {g}")
    if not (added or removed or missing_groups):
        print("  (already up to date)")
        return 0

    if not args.apply:
        print("\ndry run -- re-run with --apply to write")
        return 0

    # `enable` is required by this endpoint even though the read omits it.
    body = {
        "bot": {
            "enable": True,
            "bot_menu_enable": True,
            "bot_menu_display_strategy": bot.get("bot_menu_display_strategy", 3),
            "bot_menus": nested_menu(),
        }
    }

    resp = requests.patch(
        f"{API}/application/v7/applications/{app_id()}/ability",
        headers={
            "Authorization": f"Bearer {tenant_token()}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=body,
        timeout=30,
    )
    data = resp.json()
    print(f"\nPATCH -> HTTP {resp.status_code} code={data.get('code')} msg={data.get('msg')}")
    if data.get("code") != 0:
        print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])
        return 1

    app2, version2, live2, _ = read_state(cli)
    g2 = len({n.get("parent_menu_id") for n in live2 if n.get("parent_menu_id")})
    i2 = sum(1 for n in live2 if n.get("parent_menu_id"))
    print(f"read back: version {version2}, {g2} groups / {i2} items ({len(live2)} nodes)")
    if app2.get("online_version_id") != app.get("online_version_id"):
        print(f"online version changed: {app.get('online_version_id')} -> "
              f"{app2.get('online_version_id')}  (it is live)")
    else:
        print("online version unchanged -- the write landed in a draft, "
              "which must be published before the client shows it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
