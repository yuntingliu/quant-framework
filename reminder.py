from flask import Flask, request
import requests

app = Flask(__name__)

FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/94cf0dbb-e2e3-4ca9-b8f4-1d904f068d18"


@app.route("/git-webhook", methods=["POST"])
def webhook():

    data = request.json

    repo = data.get("repository", {}).get("full_name", "")
    user = data.get("sender", {}).get("login", "")

    commit = ""
    if data.get("head_commit"):
        commit = data["head_commit"].get("message", "")

    msg = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "🚀 Git仓库更新"}},
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"""
**仓库**
{repo}

**作者**
{user}

**Commit**
{commit}
""",
                    },
                }
            ],
        },
    }

    requests.post(FEISHU_WEBHOOK, json=msg)

    return "ok"


app.run(host="0.0.0.0", port=8000)
