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


def get_beijing_time():
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def mask_string(s):
    """仅仅用于失败账号的脱敏显示，方便认出是哪个号又不会泄露"""
    if not s:
        return "未知"
    s = str(s).strip()
    if len(s) <= 6:
        return s
    return s[:4] + "****" + s[-4:]


def send_wecom(title, content):
    """企业微信群机器人 Webhook 推送"""
    webhook = os.getenv('WECOM_WEBHOOK')

    if not webhook:
        print("未配置企业微信机器人 WECOM_WEBHOOK 环境变量，跳过推送。")
        return

    try:
        message = {
            "msgtype": "text",
            "text": {
                # [修改点]：这里把 f"{title}\n\n{content}" 改成了 f"{title}\n{content}"
                "content": f"{title}\n{content}"
            }
        }
        res = requests.post(webhook, json=message).json()
        if res.get('errcode') == 0:
            print("企业微信机器人推送成功！")
        else:
            print(f"企业微信机器人推送失败: {res}")
    except Exception as e:
        print(f"企业微信机器人推送过程发生异常: {e}")


def parse_users():
    raw = os.getenv("ACCOUNTS", "")
    accounts = []
    for item in str(raw or "").splitlines():
        item = item.strip()
        if item:
            accounts.append(item)
    return accounts


def encrypt_payload(payload):
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


class TomatoClient:
    def __init__(self, account_param):
        self.account_param = account_param
        self.tomato_user_id = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "Origin": BASE_URL,
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


def process_user(account_param, index):
    """
    处理单个账号
    返回: (是否成功: bool, 日志列表: list)
    """
    logs = [f"【账号 {index}】"]
    client = TomatoClient(account_param)

    # 尝试登录
    try:
        client.login()
    except Exception as exc:
        logs.append(f"❌ 登录失败：{exc}")
        return False, logs  # 登录失败，直接返回 False

    # 后台静默执行任务，不再打印详细过程
    completed = 0
    skipped = 0
    friend_task = None
    
    try:
        for task in client.tasks():
            task_type = task.get("taskType")
            if task_type == FRIEND_TASK_TYPE:
                friend_task = task
                continue
            if str(task.get("completed")) == "1":
                continue
            if task_type not in SUPPORTED_TASK_TYPES:
                skipped += 1
                continue
            try:
                client.complete_task(task)
                completed += 1
            except Exception:
                pass
            time.sleep(random.uniform(2.5, 3.5))
    except Exception:
        pass

    # 后台静默收取好友能量
    try:
        claimable_friends = [
            friend for friend in client.friends()
            if str(friend.get("friendStatus")) == FRIEND_STATUS_CLAIMABLE
            and friend.get("friendTomatoUserId")
        ]
        stolen_count = 0
        for friend in claimable_friends:
            friend_user_id = friend["friendTomatoUserId"]
            try:
                friend_home = client.friend_home(friend_user_id)
                amount = int(friend_home.get("stealAmount") or 0)
                if str(friend_home.get("canSteal")) == "1" and amount > 0:
                    client.steal_friend_energy(friend_user_id)
                    stolen_count += 1
            except Exception:
                pass
            time.sleep(random.uniform(1.5, 2.5))

        if stolen_count and friend_task and str(friend_task.get("completed")) != "1":
            completed += 1
    except Exception:
        pass

    # 后台静默使用能量
    try:
        home = client.home()
        energy = int(home.get("energyBalance") or 0)
        if energy > 0:
            client.use_energy()
    except Exception:
        pass

    logs.append(f"✅ 任务状态：成功完成 {completed} 个，跳过 {skipped} 个")
    return True, logs  # 正常跑完返回 True


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

    total_accounts = len(users)
    success_count = 0
    failed_wids = []  # 记录失败账号的列表

    print(f"[{bj_time}] 开始执行任务，共检测到 {total_accounts} 个账号")

    for index, account_param in enumerate(users, 1):
        print(f"\n===== 开始处理账号 {index} =====")
        try:
            is_success, logs = process_user(account_param, index)
            if is_success:
                success_count += 1
            else:
                # 记录失败的账号和脱敏 wid
                failed_wids.append(f"账号 {index}: {mask_string(account_param)}")
        except Exception as exc:
            logs = [
                f"【账号 {index}】",
                f"❌ 处理异常：{exc}",
            ]
            failed_wids.append(f"账号 {index}: {mask_string(account_param)}")
            
        print("\n".join(logs))
        
        if index < total_accounts:
            time.sleep(random.uniform(3, 5))

    failed_count = total_accounts - success_count
    
    # 构建推送文本
    summary_title = "🍅 统一茄皇每日任务报告"
    summary_stats = (
        f"⏰ 执行时间：{bj_time}\n"
        f"📊 账号总数：{total_accounts} 个\n"
        f"🟢 成功运行：{success_count} 个\n"
        f"🔴 失败数量：{failed_count} 个"
    )
    
    # 如果有失败的账号，追加到推送消息末尾
    if failed_wids:
        summary_stats += "\n\n⚠️ 失败账号列表：\n" + "\n".join(failed_wids)
    
    send_wecom(summary_title, summary_stats)


if __name__ == "__main__":
    main()
