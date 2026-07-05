# -*- coding: utf-8 -*-
"""
公司股價長期歷史查詢（判斷「會不會大漲」）— v2 增量更新版
==========================================================
v2 改了什麼：
  1. 修正舊版「抓過就永遠跳過」的問題 → 已抓過的公司會自動「補抓」
     上次之後的新資料（增量更新），只有新公司才抓完整10年。
  2. 每次更新後重算最低/最高/漲幅等統計。
  3. json 內新增 _updated 欄位記錄資料更新日，stock_viewer.html 會顯示。
  4. 自動從最新的 已發行CB_*.csv 補齊公司名。

來源：FinMind（上市櫃皆可，免登入；額度300次/hr）。
依賴：pip install requests pandas
用法：
  python stock_history.py                  # 更新 已發行CB_*.csv 全部發債公司（增量）
  python stock_history.py 1560 6274        # 只更新指定幾家（增量）
  python stock_history.py --full 1560      # 強制整段重抓指定幾家
  python stock_history.py --full           # 強制全部重抓（很慢，非必要別用）
"""
import os, sys, json, time, datetime as dt
import requests, urllib3
import pandas as pd
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(HERE, "watch_stocks.txt")
OUTPATH = os.path.join(HERE, "stock_history.json")
YEARS = 10
NAME_MAP = {}
KY_SET = set()   # KY 股代碼（自動從 CSV 判斷，一律排除）

def fetch(stock, start, end):
    """抓 start~end 的日收盤價，回傳 [(date, close), ...]（已排序）"""
    url = "https://api.finmindtrade.com/api/v4/data"
    p = {"dataset": "TaiwanStockPrice", "data_id": str(stock),
         "start_date": start.isoformat(), "end_date": end.isoformat()}
    for attempt in range(6):
        try:
            r = requests.get(url, params=p, timeout=60)
            if r.status_code in (402, 429):  # 限流
                print("  限流，等120秒再試...")
                time.sleep(120)
                continue
            j = r.json()
            if "upper limit" in str(j.get("msg", "")):
                print("  達額度上限（300次/hr），等120秒...")
                time.sleep(120)
                continue
            out = []
            for row in j.get("data", []):
                try:
                    d = dt.date.fromisoformat(row["date"])
                    c = float(row["close"])
                    if c > 0:
                        out.append((d, c))
                except Exception:
                    pass
            out.sort()
            return out
        except Exception as e:
            print("  抓取失敗，重試:", e)
            time.sleep(5)
    return []

def parse_csv():
    """讀最新的 已發行CB_*.csv：填 NAME_MAP、找出 KY 股(KY_SET)、回傳非KY代碼清單"""
    import glob
    files = sorted(glob.glob(os.path.join(HERE, "已發行CB_*.csv")))
    if not files:
        return []
    df = pd.read_csv(files[-1], dtype=str)
    df.columns = [c.strip() for c in df.columns]
    col = "標的股代碼" if "標的股代碼" in df.columns else None
    namecol = "可轉債簡稱" if "可轉債簡稱" in df.columns else None
    if not col:
        return []
    codes = []
    for _, r in df.iterrows():
        code = str(r[col]).strip()
        if not code.isdigit():
            continue
        raw = str(r[namecol]).strip() if namecol else ""
        if "KY" in raw.upper():           # KY 股：記下代碼，一律排除
            KY_SET.add(code)
            continue
        if code not in NAME_MAP and raw:
            NAME_MAP[code] = raw.rstrip("一二三四五六七八九十0123456789創-").strip()
        if code not in codes:
            codes.append(code)
    print(f"從 {os.path.basename(files[-1])} 讀出 {len(codes)} 家發債公司"
          f"（已排除 {len(KY_SET)} 家 KY 股）")
    return codes

def load_codes(args):
    csv_codes = parse_csv()   # 不管有沒有帶參數都先讀 CSV，才知道哪些是 KY
    codes = [a for a in args if a.isdigit()]
    if codes:
        ky = [c for c in codes if c in KY_SET]
        if ky:
            print(f"這些是 KY 股，已略過：{' '.join(ky)}")
        return [c for c in codes if c not in KY_SET]
    if csv_codes:
        return csv_codes
    if os.path.exists(WATCH):
        return [l.strip().split()[0] for l in open(WATCH, encoding="utf-8")
                if l.strip() and l.strip().split()[0].isdigit()
                and l.strip().split()[0] not in KY_SET]
    # 雲端備援：沒有 CSV 也沒有 watch 清單時，沿用既有 json 裡的公司做增量更新
    if os.path.exists(OUTPATH):
        try:
            j = json.load(open(OUTPATH, encoding="utf-8"))
            codes = [k for k in j if not k.startswith("_") and k not in KY_SET]
            if codes:
                print(f"找不到 CSV，沿用 stock_history.json 既有 {len(codes)} 家做增量更新")
                return codes
        except Exception:
            pass
    print("沒給代碼，也找不到 已發行CB_*.csv")
    return []

def recompute(code, series):
    """由完整 series 重算統計。series = [(date, close), ...]"""
    closes = [c for _, c in series]
    lo, hi = min(closes), max(closes)
    first, last = closes[0], closes[-1]
    lo_date = next(d for d, c in series if c == lo)
    hi_date = next(d for d, c in series if c == hi)
    return {
        "stock": code,
        "name": NAME_MAP.get(code, ""),
        "start": series[0][0].isoformat(), "end": series[-1][0].isoformat(),
        "low": round(lo, 2), "low_date": lo_date.isoformat(),
        "high": round(hi, 2), "high_date": hi_date.isoformat(),
        "first": round(first, 2), "last": round(last, 2),
        "max_gain": round(hi / lo, 2),
        "from_first": round(last / first, 2),
        "total_days": len(series),
        "series": [{"date": d.isoformat(), "close": round(c, 2)} for d, c in series],
    }

def save(result):
    result["_updated"] = dt.date.today().isoformat()
    json.dump(result, open(OUTPATH, "w", encoding="utf-8"), ensure_ascii=False)

def main():
    args = sys.argv[1:]
    full = "--full" in args
    if full:
        args = [a for a in args if a != "--full"]
    codes = load_codes(args)
    if not codes:
        return

    today = dt.date.today()
    default_start = dt.date(today.year - YEARS, today.month, today.day)

    result = {}
    if os.path.exists(OUTPATH):
        try:
            result = json.load(open(OUTPATH, encoding="utf-8"))
        except Exception:
            result = {}
    result.pop("_updated", None)  # 舊的 meta 拿掉，最後重寫

    # 把 json 裡已存在的 KY 股資料刪掉（依名稱含KY再保險比對一次）
    removed = [k for k in list(result) if k in KY_SET
               or "KY" in str(result[k].get("name", "")).upper()]
    for k in removed:
        result.pop(k)
    if removed:
        print(f"已從 json 移除 {len(removed)} 家 KY 股：{' '.join(sorted(removed))}")
        save(result)

    n_new = n_inc = n_skip = 0
    for idx, code in enumerate(codes, 1):
        entry = result.get(code)
        old_name = (entry or {}).get("name", "")

        if entry and entry.get("series") and not full:
            # ---- 增量更新：從最後一天的隔天補抓到今天 ----
            last_date = dt.date.fromisoformat(entry["series"][-1]["date"])
            if last_date >= today:
                n_skip += 1
                continue
            new = fetch(code, last_date + dt.timedelta(days=1), today)
            new = [(d, c) for d, c in new if d > last_date]
            if not new:
                # 沒有新交易日（假日/剛更新過/已下市），只補公司名
                if not entry.get("name") and NAME_MAP.get(code):
                    entry["name"] = NAME_MAP[code]
                n_skip += 1
                continue
            series = [(dt.date.fromisoformat(r["date"]), float(r["close"]))
                      for r in entry["series"]] + new
            result[code] = recompute(code, series)
            if not result[code]["name"]:
                result[code]["name"] = old_name
            n_inc += 1
            print(f"({idx}/{len(codes)}) {code} 補 {len(new)} 日 → 更新至 {series[-1][0]}")
        else:
            # ---- 新公司（或 --full 強制重抓）：抓完整10年 ----
            print(f"({idx}/{len(codes)}) {code} 抓近{YEARS}年完整股價...")
            s = fetch(code, default_start, today)
            if not s:
                print(f"  {code} 查無資料")
                continue
            result[code] = recompute(code, s)
            if not result[code]["name"]:
                result[code]["name"] = old_name
            n_new += 1
            print(f"  ✓ {len(s)}日 最大漲幅{result[code]['max_gain']}倍")

        save(result)  # 邊抓邊存，中斷也不會白做
        time.sleep(0.6)

    save(result)
    total = len([k for k in result if not k.startswith("_")])
    print(f"\n完成！新抓 {n_new} 家、增量更新 {n_inc} 家、已是最新 {n_skip} 家，共 {total} 家。")
    print(f"資料更新日：{today}。用 stock_viewer.html 開啟 stock_history.json。")

if __name__ == "__main__":
    main()
