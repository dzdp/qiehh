import base64
import json
import os
import random
import time
from datetime import datetime, timezone, timedelta

import requests

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    CRYPTO_BACKEND = "cryptography"
except ImportError:
    try:
        from Crypto.Cipher import AES, PKCS1_OAEP
        from Crypto.Hash import SHA256
        from Crypto.PublicKey import RSA

        CRYPTO_BACKEND = "pycryptodome"
    except ImportError:
        CRYPTO_BACKEND = None


BASE_URL = "https://farmgames.ioutu.cn"
PUBLIC_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA70sK419vy3MabW3lEGlk"
    "7Zh1u78OdnVlioVazp5Y46eBh+/TDqo/wZ9VrQ/4MmAtoP0vJ2vmwP5gqO3WPoj"
    "b07WddXfF1eU+5M+Rj3s0eSRrvZvBcGZ3qK0dOgZJScK66IDQazt/c4xqhDcsI"
    "tIyNRahUqB/IKc6E80GZJvMvFtZVSCseAXC0mAJXhi1AdUOlP+3Pv0fiUVejTJp"
    "1j7LBNWJ7Z5/8mRcclQH0vmxsdYsaV3qZiJ2d/CfNoKcwmI2IWmeZy8NP5U8Hn"
    "0AsxPEwjdHoEqG/iy/SoA46TZL+RLtWqUSHXpaKR/VFN0rbl25SE91X8FTfLqyD"
    "8LfGMCwRQIDAQAB"
)
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.75(0x18004b43) NetType/WIFI Language/zh_CN "
    "miniProgram/wx532ecb3bdaaf92f9"
)
SUPPORTED_TASK_TYPES = {"SIGN", "BROWSE", "SHARE"}
FRIEND_TASK_TYPE = "FRIEND_STEAL_ENERGY"
FRIEND_STATUS_CLAIMABLE = "0"


# ---------------- 工具函数 ----------------

def get_beijing_time():
    """获取当前北京时间"""
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def send_wecom(title, content):
    """企业微信应用推送"""
    corpid = os.getenv('WECOM_CORPID')
    corpsecret = os.getenv('WECOM_CORPSECRET')
    agentid = os.getenv('WECOM_AGENTID')
    touser = os.getenv('WECOM_TOUSER', '@all')

    if not all([corpid, corpsecret, agentid]):
        print("未配置企业微信推送环境变量，跳过推送。")
        return

    try:
        token_url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corpid}&corpsecret={corpsecret}"
        res = requests.get(token_url).json()
        if res.get('errcode') != 0:
            print(f"获取企业微信 Token 失败: {res}")
            return
        access_token = res['access_token']

        send_url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
        message = {
            "touser": touser,
            "msgtype": "text",
            "agentid": int(agentid),
            "text": {
                "content": f"{title}\n\n{content}"
            },
            "safe": 0
        }
        res = requests.post(send_url, json=message).json()
        if res.get('errcode') == 0:
            print("企业微信推送成功！")
        else:
            print(f"企业微信推送失败: {res}")
    except Exception as e:
        print(f"企业微信推送过程发生异常: {e}")


def parse_users():
    """
    解析环境变量：单参数格式，多账号换行分隔。
    """
    raw = os.getenv("ACCOUNTS", "")
    accounts = []
    # 直接按换行符切割
    for item in str(raw or "").splitlines():
        item = item.strip()
        if item:
            accounts.append(item)
    return accounts


def encrypt_payload(payload):
    """Match the H5 client: RSA-OAEP-SHA256 + AES-256-GCM."""
    if CRYPTO_BACKEND is None:
        raise RuntimeError("缺少加密依赖，请安装 cryptography（pip install cryptography）")

    plaintext = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    aes_key = os.urandom(32)
    iv = os.urandom(12)
    public_key_der = base64.b64decode(PUBLIC_KEY)

    if CRYPTO_BACKEND == "cryptography":
        public_key = serialization.load_der_public_key(public_key_der)
        encrypted_data = AESGCM(aes_key).encrypt(iv, plaintext, None)
        encrypted_key = public_key.encrypt(
            aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    else:
        cipher = AES.new(aes_key, AES.MODE_GCM, nonce=iv)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext)
        encrypted_data = ciphertext + tag
        public_key = RSA.import_key(public_key_der)
        encrypted_key = PKCS1_OAEP.new(public_key, hashAlgo=SHA256).encrypt(aes_key)

    return {
        "data": base64.b64encode(encrypted_data).decode(),
        "key": base64.b64encode(encrypted_key).decode(),
        "iv": base64.b64encode(iv).decode(),
    }


# ---------------- 核心业务类 ----------------

class TomatoClient:
    def __init__(self, account_param):
        self.account_param = account_param  # 目前只需要一个参数，这里作为 wid
        self.tomato_user_id = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "Origin": BASE_URL,
                # 修改点1：请求头里的 Referer 传递 wid 参数
                "Referer": f"{BASE_URL}/?wid={account_param}",
            }
        )

    def request(self, method, path, payload=None, encrypted=True, retry=2):
        url = f"{BASE_URL}{path}"
        for attempt in range(retry + 1):
            kwargs = {"timeout": 20}
            if payload:
                kwargs["json"] = encrypt_payload(payload) if encrypted else payload
                if encrypted:
                    kwargs["headers"] = {"X-Request-Encrypted": "true"}
            response = self.session.request(method, url, **kwargs)
            if response.status_code == 429 and attempt < retry:
                retry_after = response.headers.get("Retry-After", "2")
                try:
                    wait_seconds = max(1.0, float(retry_after))
                except ValueError:
                    wait_seconds = 2.0
                time.sleep(wait_seconds + attempt)
                continue
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError as exc:
                raise RuntimeError(f"接口返回非 JSON 数据：{response.text[:200]}") from exc

            msg = str(result.get("msg", ""))
            if result.get("code") == 200:
                return result
            if attempt < retry and (response.status_code == 429 or "频繁" in msg or "稍后" in msg):
                time.sleep(2.5 + attempt * 1.5)
                continue
            raise RuntimeError(msg or f"接口返回 code={result.get('code')}")
        raise RuntimeError("请求重试后仍未成功")

    def login(self):
        result = self.request(
            "POST",
            "/api/web/open/tomato/login",
            {
                "shareTomatoUserId": None,
                # 修改点2：将单参数作为 wid 传入，openId 留空
                "openId": "",                     
                "wid": self.account_param,
                "queryCardStatus": True,
            },
        )
        data = result.get("data") or {}
        token = data.get("token")
        if not token:
            raise RuntimeError("登录响应中没有 token")
        self.session.headers["Authorization"] = token
        self.tomato_user_id = data.get("tomatoUserId")
        return data

    def home(self):
        return self.request("GET", "/api/web/member/tomato/home").get("data") or {}

    def tasks(self):
        return self.request("GET", "/api/web/member/tomato/tasks").get("data") or []

    def complete_task(self, task):
        task_type = task.get("taskType")
        payload = {"taskType": task_type}
        if task_type != "SHARE":
            payload["browseTarget"] = task.get("browseTarget") or ""
        elif self.tomato_user_id:
            try:
                self.request(
                    "POST",
                    "/api/web/member/tomato/miniprogram/qrcode/create",
                    {
                        "page": "packages/wm-cloud-qiehuang/home/index",
                        "scene": str(self.tomato_user_id),
                    },
                )
            except Exception:
                pass
        return self.request(
            "POST", "/api/web/member/tomato/tasks/complete", payload
        ).get("data") or {}

    def friends(self, page_size=20):
        friends = []
        page_num = 1
        while True:
            result = self.request(
                "GET",
                f"/api/web/member/tomato/friends?pageNum={page_num}&pageSize={page_size}",
            )
            rows = result.get("rows") or []
            friends.extend(rows)
            total = int(result.get("total") or 0)
            if not rows or (total and len(friends) >= total) or len(rows) < page_size:
                break
            page_num += 1
        return friends

    def friend_home(self, friend_user_id):
        return self.request(
            "GET",
            f"/api/web/member/tomato/friends/{friend_user_id}/home",
        ).get("data") or {}

    def steal_friend_energy(self, friend_user_id):
        return self.request(
            "POST",
            "/api/web/member/tomato/friends/steal",
            {"friendTomatoUserId": friend_user_id},
        ).get("data")

    def use_energy(self):
        return self.request(
            "POST", "/api/web/member/tomato/energy/use", encrypted=False
        ).get("data") or {}


def home_line(data, prefix="当前状态"):
    return (
        f"{prefix}：能量 {data.get('energyBalance', 0)}，"
        f"番茄 {data.get('tomatoBalance', 0)}，"
        f"{data.get('stageName', '未知阶段')} "
        f"{data.get('currentExp', 0)}/{data.get('stageRequiredExp', 0)}"
    )


def process_user(account_param, index):
    logs = [f"【账号 {index}】"]
    client = TomatoClient(account_param)

    login_data = client.login()
    logs.append(f"登录成功：{login_data.get('nickName') or '未设置昵称'}")
    home = client.home()
    logs.append(home_line(home))

    completed = 0
    skipped = 0
    friend_task = None
    for task in client.tasks():
        name = task.get("taskName") or task.get("taskCode") or "未知任务"
        task_type = task.get("taskType")
        if task_type == FRIEND_TASK_TYPE:
            friend_task = task
            if str(task.get("completed")) == "1":
                logs.append(f"任务已完成：{name}")
            continue
        if str(task.get("completed")) == "1":
            logs.append(f"任务已完成：{name}")
            continue
        if task_type not in SUPPORTED_TASK_TYPES:
            skipped += 1
            logs.append(f"跳过任务：{name}（需在小程序内操作）")
            continue
        try:
            result = client.complete_task(task)
            reward = result.get("rewardText") or task.get("rewardText") or "已领取"
            logs.append(f"任务完成：{name}，{reward}")
            completed += 1
        except Exception as exc:
            logs.append(f"任务失败：{name}，{exc}")
        time.sleep(random.uniform(2.5, 3.5))

    try:
        claimable_friends = [
            friend
            for friend in client.friends()
            if str(friend.get("friendStatus")) == FRIEND_STATUS_CLAIMABLE
            and friend.get("friendTomatoUserId")
        ]
        stolen_count = 0
        stolen_energy = 0
        failed_count = 0
        for friend in claimable_friends:
            friend_user_id = friend["friendTomatoUserId"]
            try:
                friend_home = client.friend_home(friend_user_id)
                amount = int(friend_home.get("stealAmount") or 0)
                if str(friend_home.get("canSteal")) != "1" or amount <= 0:
                    continue
                client.steal_friend_energy(friend_user_id)
                stolen_count += 1
                stolen_energy += amount
            except Exception:
                failed_count += 1
            time.sleep(random.uniform(1.5, 2.5))

        if stolen_count:
            detail = f"好友能量：成功收取 {stolen_count} 位好友，共 {stolen_energy} 能量"
            if failed_count:
                detail += f"，失败 {failed_count} 位"
            logs.append(detail)
            if friend_task and str(friend_task.get("completed")) != "1":
                completed += 1
        elif failed_count:
            logs.append(f"好友能量：收取失败 {failed_count} 位")
        else:
            logs.append("好友能量：暂无可收取能量")
    except Exception as exc:
        logs.append(f"好友能量失败：{exc}")

    home = client.home()
    logs.append(home_line(home, "任务后状态"))
    energy = int(home.get("energyBalance") or 0)
    if energy > 0:
        before_tomato = int(home.get("tomatoBalance") or 0)
        try:
            grown = client.use_energy()
            after_tomato = int(grown.get("tomatoBalance") or 0)
            gained = int(grown.get("gainedTomatoAmount") or 0)
            if not gained:
                gained = max(0, after_tomato - before_tomato)
            logs.append(
                f"使用能量：消耗 {grown.get('usedEnergyAmount', energy)}，"
                f"成长到 {grown.get('stageName', '未知阶段')} "
                f"{grown.get('currentExp', 0)}/{grown.get('stageRequiredExp', 0)}，"
                f"获得番茄 {gained}"
            )
            home = grown
        except Exception as exc:
            logs.append(f"使用能量失败：{exc}")
    else:
        logs.append("使用能量：当前没有可用能量")

    logs.append(home_line(home, "最终状态"))
    logs.append(f"本次完成任务 {completed} 个，跳过 {skipped} 个")
    return logs


def main():
    users = parse_users()
    bj_time = get_beijing_time()
    
    if CRYPTO_BACKEND is None:
        message = "缺少加密依赖，请安装 cryptography"
        print(message)
        send_wecom("统一茄皇运行异常", message)
        return
        
    if not users:
        message = "没有可用账号：未读取到 ACCOUNTS 环境变量，请确保已配置。"
        print(message)
        send_wecom("统一茄皇运行异常", message)
        return

    all_logs = []
    total_accounts = len(users)
    success_count = 0

    print(f"[{bj_time}] 开始执行任务，共检测到 {total_accounts} 个账号")

    # 单参数直接传入即可
    for index, account_param in enumerate(users, 1):
        print(f"\n===== 开始处理账号 {index} =====")
        try:
            logs = process_user(account_param, index)
            success_count += 1
        except Exception as exc:
            logs = [
                f"【账号 {index}】",
                f"❌ 处理失败：{exc}",
            ]
        all_logs.append(logs)
        print("\n".join(logs))
        
        if index < total_accounts:
            time.sleep(random.uniform(3, 5))

    # 汇总运行报告
    failed_count = total_accounts - success_count
    
    summary_title = "🍅 统一茄皇每日任务报告"
    summary_stats = (
        f"⏰ 执行时间：{bj_time}\n"
        f"📊 账号总数：{total_accounts} 个\n"
        f"🟢 成功运行：{success_count} 个\n"
        f"🔴 失败数量：{failed_count} 个\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
    )
    
    # 拼接详细日志
    detail_lines = []
    for logs in all_logs:
        detail_lines.extend(logs)
        detail_lines.append("━━━━━━━━━━━━━━━━━━━━")
        
    full_report = summary_stats + "\n".join(detail_lines)
    
    # 企业微信推送
    send_wecom(summary_title, full_report)


if __name__ == "__main__":
    main()

