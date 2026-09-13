# auto-news-web

汽车资讯聚合站前端原型，英文界面，视觉风格参考 AIHOT：240px 固定侧栏、三栏时间轴、
通栏热点榜、顶部品牌栏、token 化的暗/亮双主题。

数据来自 [auto-news-monitor](../auto-news-monitor) 抓取的易车 / 新浪汽车 / 汽车之家，
以 JSON 快照形式打进构建，暂不连后端。

## 用法

```
npm install
npm run data     # 重新生成 src/data/news.json（读 digest + 翻译 + 品牌 + 汇率）
npm run dev      # http://localhost:3000
```

`npm run data` 会联网拉一次汇率；断网时自动退回兜底值并在页面上标记。

## 结构

```
scripts/build-data.py       digest jsonl -> src/data/news.json
src/data/
  news.json                 数据快照（构建产物，可重新生成）
  translations.en.json      英文翻译缓存，按条目 id 索引 —— 手工维护
  brands.json               品牌配置：别名、出海排序、来源地 —— 手工维护
src/lib/news.ts             数据读取、分组、榜单、日期格式化
src/app/                    / 精选、/daily 日报、/ranking 热点榜、/about 说明
src/components/
  Sidebar                   侧栏导航 + 底部两个开关
  ThemeSwitch               暗 / 跟随系统 / 亮
  CurrencySwitch            ¥ / ¥$ / $
  CurrencyProvider          货币模式 context，汇率由 layout 从快照注入
  Money                     把文本里的 ¥ 金额按当前模式渲染
  BrandBar                  顶部品牌栏
  Feed                      品牌 + 分类 + 搜索三重筛选，交给 Timeline 渲染
  Timeline / NewsCard / HotBoard / HeatBadge
```

## 英文化

站点界面和内容都是英文。**源数据是中文**，标题和要点的翻译存在
`src/data/translations.en.json`，按条目 id 索引。构建时命中就用英文，
没命中则回退中文原文并把 `translated` 标记为 false，`/about` 页会显示未翻译条数。

重跑 `npm run data` 时，抓取端新增的条目不会自动翻译——需要往翻译文件里补，
或者改 `auto-news-monitor` 的 DeepSeek 提示词直接产出双语。

## 货币转换

价格**默认只显示美元**（`$57,800`），换算值带一条淡虚线，hover 可看到原价和所用汇率。
侧栏的 ¥ / ¥$ / $ 开关可切换成只看人民币或两者并列。选择存在 `localStorage.currency`。

**「万」的歧义在翻译阶段就消解掉了**，这是这套方案的关键：中文原文里
`38万元` 是钱、`120万台` 是数量，正则无法可靠区分。翻译时人工判断，
钱统一写成 `¥380,000`，数量写成 `1.2M units`。所以运行时 `Money` 组件
只需匹配明确的 `¥` 模式，不用再猜哪个数字是价格。

汇率构建时从 `open.er-api.com` 拉取并固化进快照（前端不发网络请求）。
拉取失败时用 `FALLBACK_CNY_USD` 兜底，快照里 `rate.stale=true`，
货币开关上会出现琥珀色提示点，`/about` 页也会说明。
换算值保留 3 位有效数字，避免 `$13,311.85` 这种伪精度。

## 品牌索引

顶部一栏平铺全部品牌，不进二级菜单。点击按品牌筛选，与分类、搜索三者联动
（分类计数跟随品牌，品牌计数不跟随分类，避免数字来回跳）。

`src/data/brands.json` 是唯一配置来源，Python 和前端共用：

- `aliases` 用于从英文标题/要点里匹配品牌，匹配时长别名优先，
  保证 `Dongfeng Nissan` 不被 `Nissan` 抢走。合资公司名归到母品牌。
- `exportRank` 决定品牌栏顺序。**当前是按公开认知给的估计值，不是真实销量数据**，
  拿到实际的海外销量后直接改这个字段即可。
- `origin=CN` 的品牌排在 `INTL` 前面，国际品牌的 wordmark 会弱一档。

品牌标识目前渲染**文字 wordmark**。真 logo 有商标权，需要自备文件：
放到 `public/brands/<slug>.svg` 后，改 `BrandBar.tsx` 的 `BrandMark` 换成 `<img>` 即可，
其余布局不用动。

## 设计 token

- 配色：暗色为默认，主 accent 是刹车灯橙红 `#e2603f`；
  分类映射 Product Launch=电动蓝 / Market Data=新能源绿 / Industry Buzz=橙红
- 时长：`--dur-press 80ms` / `--dur-fast 120ms` / `--dur-base 160ms` / `--dur-enter 240ms`
- 主题切换过渡统一走 `--theme-transition`

主题偏好存在 `localStorage.theme`（`dark` / `system` / `light`），货币模式存 `localStorage.currency`。
`layout.tsx` 的内联脚本在首帧前把主题偏好解析成具体的 `dark`/`light` 写入 `<html data-theme>`，
所以 CSS 只需要认两种值，不必为「跟随系统」重复一份变量。

## 截图验证

本机 Chrome 可直接跑 headless 截图。注意 **headless 窗口最小宽度是 500px**，
截移动端要用 CDP 的 `Emulation.setDeviceMetricsOverride`，否则页面按 500px 渲染、
截图再裁到目标宽度，会看起来像横向溢出。

```
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
  --blink-settings=preferredColorScheme=2 --window-size=1280,1100 \
  --screenshot=out.png --user-data-dir=/tmp/cp "http://localhost:3000/"
```

`preferredColorScheme` 取 `1` 是亮色、`2` 是暗色。
要测交互（点品牌、切筛选），用 `--remote-debugging-port=9222` 起 Chrome，
再用 CDP 的 `Runtime.evaluate` 触发点击后截图。

## 已知限制

- **关注度（heat）是占位值**，由 `compute_heat()` 按分类/来源/标题关键词派生，
  不是真实互动数据。`featured` 取每天 heat 最高的一条，同样受此影响。
- **`exportRank` 是估计值**，需要用真实海外销量校正。
- **品牌 logo 缺失**，当前是文字 wordmark。
- 数据是构建时快照，不会自动更新；抓取端产出新数据后需重跑 `npm run data`，
  新条目需要补翻译。
- 快照当前覆盖 2026-06-15 ~ 2026-08-01 共 76 条，「今天」指快照内最新一天。
- 数据分布偏斜：Product Launch 66 条、Market Data 9 条、Industry Buzz 1 条。
- 卡片没有「推荐理由」层（AIHOT 有），因为 digest 数据里没有这个字段。
