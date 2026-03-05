import aiohttp
import logging
import uuid
import json
from datetime import datetime

class VPNService:
    def __init__(self, panel_url: str, public_ip: str, username: str, password: str, inbound_id: int, sni: str, pbk: str, sid: str):
        self.api_url = panel_url.rstrip('/') 
        self.public_ip = public_ip
        self.username = username
        self.password = password
        
        self.inbound_id = inbound_id
        self.sni = sni
        self.pbk = pbk
        self.sid = sid

    async def _login(self, session: aiohttp.ClientSession):
        url = f"{self.api_url}/login"
        data = {"username": self.username, "password": self.password}
        async with session.post(url, data=data, ssl=False) as response:
            if response.status != 200:
                raise Exception(f"Ошибка авторизации (HTTP {response.status})")
            res = await response.json()
            if not res.get("success"):
                raise Exception("Панель отклонила логин/пароль.")

    async def _add_client_request(self, session: aiohttp.ClientSession, client_uuid: str, email: str, expiry_date: datetime = None):
        url = f"{self.api_url}/panel/api/inbounds/addClient"
        
        expiry_time_ms = int(expiry_date.timestamp() * 1000) if expiry_date else 0
        
        client_dict = {
            "id": client_uuid,
            "email": email,
            "flow": "xtls-rprx-vision",
            "limitIp": 1, 
            "totalGB": 0,
            "expiryTime": expiry_time_ms,
            "enable": True,
            "tgId": "",
            "subId": ""
        }
        
        payload = {
            "id": self.inbound_id,
            "settings": json.dumps({"clients": [client_dict]})
        }
        
        async with session.post(url, json=payload, ssl=False) as response:
            if response.status != 200:
                raise Exception(f"Ошибка добавления клиента (HTTP {response.status}). URL: {url}")
            res = await response.json()
            if not res.get("success"):
                raise Exception(f"Панель вернула ошибку: {res}")

    async def get_happ_key_for_user(self, telegram_id: int, expiry_date: datetime = None) -> str:
        client_uuid = str(uuid.uuid4())
        email = f"tg_{telegram_id}_{client_uuid[:6]}"
        
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar) as session:
            await self._login(session)
            await self._add_client_request(session, client_uuid, email, expiry_date)
            
        vless_port = 443 
        link = (
            f"vless://{client_uuid}@{self.public_ip}:{vless_port}"
            f"?type=tcp&security=reality&pbk={self.pbk}&fp=chrome&sni={self.sni}"
            f"&sid={self.sid}&spx=%2F&flow=xtls-rprx-vision&alpn=h2,http/1.1&headerType=none"
            f"#{email}"
        )
        return link

    async def update_client_expiry(self, client_uuid: str, email: str, expiry_date: datetime):
        url = f"{self.api_url}/panel/api/inbounds/updateClient/{client_uuid}"
        expiry_time_ms = int(expiry_date.timestamp() * 1000) if expiry_date else 0
        
        client_dict = {
            "id": client_uuid,
            "email": email,
            "flow": "xtls-rprx-vision",
            "limitIp": 1,
            "totalGB": 0,
            "expiryTime": expiry_time_ms,
            "enable": True,
            "tgId": "",
            "subId": ""
        }
        
        payload = {
            "id": self.inbound_id,
            "settings": json.dumps({"clients": [client_dict]})
        }
        
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar) as session:
            await self._login(session)
            async with session.post(url, json=payload, ssl=False) as response:
                if response.status != 200:
                    raise Exception(f"Ошибка обновления клиента (HTTP {response.status})")
                res = await response.json()
                if not res.get("success"):
                    raise Exception(f"Панель вернула ошибку при обновлении: {res}")
