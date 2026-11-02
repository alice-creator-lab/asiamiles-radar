# Asia Miles 直飛兌換雷達

揀一個日期、出發機場同艙等，一次過睇晒前後 ±N 日 **所有國泰直飛目的地** 嘅 Asia Miles 標準獎勵機位。

- 純靜態網頁（GitHub Pages），冇後端、冇登入、唔會掂你嘅 Asia Miles 帳戶。
- 資料由 GitHub Actions 每日香港時間 00:05 抓一次，同國泰自己嘅日曆更新同步。
- 覆蓋：國泰航空（CX）營運嘅直飛航班、標準獎勵（Standard Award）、單程、每位乘客。
- 另外列出**可用 Asia Miles 兌換嘅夥伴航空**（BA／QF／JL／AA 等），並顯示每間公司喺該航線嘅里數。

## 資料來源

國泰官網獎勵日曆嘅公開 JSON API：

```
https://api.cathaypacific.com/afr/search/availability/en.{ORIGIN}.{DEST}.{CABIN}.CX.{PAX}.{START}.{END}.json
```

- 艙等代碼：`eco` 經濟、`pey` 特選經濟、`bus` 商務、`fir` 頭等
- 每日狀態：`H` 座位較多、`L` 座位較少、`NA` 冇位
- 日曆只覆蓋**未來 180 日**，所以網頁最遠只會顯示到 180 日後
- 里程數來自 `afr/searchpanel/searchoptions/...`（標準獎勵最低里數）

## 檔案

| 路徑 | 用途 |
| --- | --- |
| `index.html` | 整個 App（單檔，無 build step） |
| `data/destinations.json` | 出發地 → 直飛目的地清單（含 IATA code） |
| `data/avail.json` | 每日重新產生嘅座位快照 |
| `data/partners.json` | 每條航線有邊啲 Asia Miles 夥伴航空 + 各自里數（每週更新） |
| `scripts/fetch_availability.py` | 抓 API → 寫 `data/avail.json` |
| `scripts/build_destinations.py` | 由 Wikipedia 航線表 + 國泰 OD API 重建目的地清單，並用日曆 API 驗證 |
| `scripts/build_partners.py` | 逐條航線探測夥伴航空同里數表 → `data/partners.json` |
| `.github/workflows/refresh.yml` | 每日 00:05 更新快照；逢星期日 00:30 更新夥伴航空表 |

## 本機開發

```bash
python3 -m http.server 8797      # 然後開 http://127.0.0.1:8797/
python3 scripts/fetch_availability.py   # 重新抓一次快照（約 1–2 分鐘）
python3 scripts/build_destinations.py   # 重建目的地清單（要連網）
python3 scripts/build_partners.py       # 重建夥伴航空表（約 20 分鐘，刻意慢慢嚟）
```

## 夥伴航空點解冇「有位／冇位」

座位日曆 API 只涵蓋國泰自己營運嘅航班 —— 傳夥伴代碼（`BA`、`QF`、`JL`…）入去
`afr/search/availability` 會回一個空日曆，即使該公司真係飛呢條航線。夥伴位表只存在於
國泰會員訂票流程（`redibe/IBEFacade`，要真實 session），所以本 app 只顯示：

- 呢條航線有邊啲夥伴航空可以用 Asia Miles 兌換（由 `afr/searchpanel/searchoptions`
  以 `airlineCode` 探測；唔飛呢條線嘅公司會回 `ERR_AFR_001`）
- 每間公司、每個艙等嘅標準獎勵里數

**唔會**憑猜測標「有位」。另外，「有里數表」只代表該公司喺呢條航線有獎勵兌換，
可能係直飛、亦可能經樞紐接駁。

## 限制

- 座位日曆只計**國泰自己營運**嘅航班：代碼共享、夥伴航空公司（BA／QF／JL 等）嘅兌換位唔會出現。
- 「H／L」係國泰日曆嘅粗粒度標示（座位較多／較少），唔係實際剩餘座位數；最終價錢同座位以官網訂票流程為準。
- 出發地選擇：國泰網絡以香港為樞紐，由其他航點出發嘅直飛目的地通常只有香港，所以介面只會顯示一條。
- 本專案與國泰航空／Asia Miles 無任何關係。
