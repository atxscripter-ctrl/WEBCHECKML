"""Playwright driver for mtacc.mobilelegends.com — WAF solved by real Chromium."""
from __future__ import annotations
import asyncio
import hashlib
import json
import random
import time
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

HOOK_JS = r"""
(function(){
  if(window.__acsHook)return; window.__acsHook=1;
  function isApi(u){u=String(u||'');return u.indexOf('accountmtapi')>=0;}
  function got(id,t,st,u,m){
    try{window.ACSBridge && window.ACSBridge.capture(JSON.stringify({
      id:id,url:u,method:m,status:st,len:(t||'').length,text:(t||'')
    }));}catch(e){}
  }
  var of=window.fetch;
  window.fetch=function(u,o){
    try{
      return of.apply(this,arguments).then(function(r){
        if(isApi(r.url||u)){
          var r2=(r.clone&&r.clone())||r;
          if(r2.text)r2.text().then(function(t){
            got(o&&o._id||'',t,r.status,r.url||u,'fetch');
          });
        }
        return r;
      });
    }catch(e){return of.apply(this,arguments);}
  };
  var OX=XMLHttpRequest.prototype,oo=OX.open,os=OX.send;
  OX.open=function(m,u){this._m=m;this._u=u;return oo.apply(this,arguments);};
  OX.send=function(b){
    try{
      var self=this;
      this.addEventListener('load',function(){
        if(isApi(self._u)){
          try{got('',self.responseText,self.status,self._u,self._m);}catch(e){}
        }
      },false);
    }catch(e){}
    return os.apply(this,arguments);
  };
})();
"""

LOGIN_URL = "https://mtacc.mobilelegends.com/"
API_BASE  = "https://accountmtapi.mobilelegends.com/"
UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Mobile Safari/537.36")


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def make_sign(account: str, md5pwd: str) -> str:
    pairs = sorted([("account", account), ("country", ""), ("md5pwd", md5pwd)])
    joined = "&".join(f"{k}={v}" for k, v in pairs)
    return _md5(joined + "&op=new_login_pwd")


def _js_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


class WebLogin:
    def __init__(self):
        self._pw = None
        self._browser: Optional[Browser] = None
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._captures: dict[str, dict] = {}

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox",
                  "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
        )
        self._ctx = await self._browser.new_context(
            user_agent=UA, locale="en-US",
            viewport={"width": 412, "height": 915},
            device_scale_factor=2.0, is_mobile=True, has_touch=True,
        )
        await self._ctx.add_init_script(HOOK_JS)
        self._page = await self._ctx.new_page()
        await self._page.expose_binding("__acsCapture", self._on_capture)
        await self._page.add_init_script(
            "window.ACSBridge = {capture: function(s){ window.__acsCapture(s); }};"
        )

    async def _on_capture(self, source, payload: str) -> None:
        try: obj = json.loads(payload)
        except Exception: return
        rid = obj.get("id") or ""
        if "accountmtapi" not in (obj.get("url") or ""): return
        if rid: self._captures[rid] = obj
        self._captures[""] = obj

    async def _wait_cookie(self, timeout_s: float = 12.0) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                ck = await self._ctx.cookies(LOGIN_URL)
                if len("; ".join(f"{c['name']}={c['value']}" for c in ck)) > 50:
                    return True
            except Exception: pass
            await asyncio.sleep(0.2)
        return False

    async def _ensure_on_login(self) -> None:
        if self._page.url != LOGIN_URL:
            await self._page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await self._page.wait_for_timeout(2500)
            await self._wait_cookie()

    async def try_login(self, account: str, password: str) -> Optional[str]:
        await self._ensure_on_login()
        md5pwd = _md5(password)
        sign = make_sign(account, md5pwd)
        payload = json.dumps({
            "op": "new_login_pwd",
            "sign": sign,
            "params": {"account": account, "md5pwd": md5pwd, "country": ""},
            "lang": "en",
        }, separators=(",", ":"), ensure_ascii=False)

        req_id = f"req-{int(time.time()*1000)}-{random.randint(0,9999)}"
        self._captures.pop(req_id, None)
        self._captures.pop("", None)

        url = API_BASE
        try:
            ck = await self._ctx.cookies(LOGIN_URL)
            jar = {c["name"]: c["value"] for c in ck}
            if "ssxmod_itna" in jar:
                url += ("?" if "?" not in url else "&") + "ssxmod_itna=" + jar["ssxmod_itna"]
        except Exception: pass

        js = (
            "(function(){try{"
            "var opts={method:'POST',credentials:'include',headers:{"
            "'Content-Type':'application/json',"
            "'Accept':'application/json, text/plain, */*',"
            "'Accept-Language':'en-GB,en-US;q=0.9,en;q=0.8',"
            "'Origin':'https://mtacc.mobilelegends.com',"
            "'Referer':'https://mtacc.mobilelegends.com/',"
            "'Sec-Fetch-Site':'same-site','Sec-Fetch-Mode':'cors','Sec-Fetch-Dest':'empty'"
            "},body:" + _js_str(payload) + ",_id:" + _js_str(req_id) + "};"
            f"fetch({_js_str(url)},opts).catch(function(e){{}});"
            "}catch(e){}})();"
        )
        await self._page.evaluate(js)

        for _ in range(6):
            await asyncio.sleep(0.5)
            cap = self._captures.get(req_id) or self._captures.get("")
            if cap and cap.get("text"): return cap["text"]
        return None

    async def stop(self) -> None:
        try:
            if self._browser: await self._browser.close()
        except Exception: pass
        try:
            if self._pw: await self._pw.stop()
        except Exception: pass