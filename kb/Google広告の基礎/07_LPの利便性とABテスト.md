# Google広告の基礎 07: ランディング ページの利便性・表示速度・A/B テスト 仕様メモ

- 確認日: 2026-11-16（公式ヘルプの日本語版と英語版、Search Central、web.dev、Chrome for Developers、論文、A/B テストツール各社の公式ドキュメントを取得して確認）
- 関連メモ: `../Google_AI最大化設定（AI Max）.md`（最終ページ URL の拡張）、`06_コンバージョン計測.md`（成果の数え方）
- 使い方: LP を直す順番や A/B テストの結果の読み方を判断する前にこのメモを読む。確認日から 30 日を過ぎていたら、下の出典を取り直してから答える。変わった点は「変更の記録」に足す
- 凡例: ✅ 公式で確認 ／ △ 公式の中で記述が食い違う（または古い記述が残る） ／ ❓ 公式に記載なし（確かめ方を併記）
- 書き分け: 「Google の公表」「第三者の検証・主張（誰のものかを明記）」「このメモの推測・計算」を分けて書く。推測には必ず「推測」と付ける
- 呼び名の注意: 日本語の呼び名は日本語ヘルプ上の表記。実際の管理画面の表記はこのメモでは見ていない。日本語ヘルプは AI 翻訳を含むと明記されている

## 1.「ランディング ページの利便性」が見ているもの

### 1-1. 定義と位置づけ ✅

| 項目 | 公式の記述 | 出典 |
|---|---|---|
| 定義 | 広告をクリックしたユーザーにとって、LP がどの程度関連性があり有用かを示す（英語名 Landing page experience） | 6167118 |
| 段階 | 「平均より上」「平均的」「平均より下」の 3 つ。キーワード単位 | 6167118 |
| 比べる相手 | 過去 90 日間に、まったく同じ検索語句で広告が表示された他の広告主 | 6167118 |
| 品質スコアとの関係 | 品質スコア（1〜10）は診断ツールで、オークションの入力ではない。オークションで使われるのは 3 要素（推定クリック率・広告の関連性・LP の利便性）のリアルタイムの評価 | 6167118、6167123 |
| 影響する先 | 広告が出るかどうか、掲載位置、アセットが出るか、クリック単価 | 156066、1722122 |
| 評価に使う情報 | Google のさまざまなクローラーから得た情報が広告の品質評価に使われることがある | 6167118 |
| 影響しないもの | 入札単価、アカウント構造、表示頻度、記録されたコンバージョンの件数 | 156066 |

- △ 用語集の記事（1659694）は比べる相手を「Google 広告全体のキーワードの平均」と書き、段階名も日本語版は「平均値」。上の 6167118（同じ検索語句の他の広告主、「平均的」）と食い違う。新しい方の 6167118 を採る
- ✅ 品質スコアの列に「—」が出るのは、キーワードと完全に一致する検索の数が足りないとき（6167118）

### 1-2.「5 項目」の出どころ △

依頼にあった 5 項目（関連性・独自性のある内容／透明性と信頼性／操作のしやすさ／読み込み速度／モバイル対応）は、旧記事「Understanding landing page experience」（answer/2404197）の見出しである。**この記事は 2026-11-16 時点で品質スコアの記事（6167118）へ転送され、本文は読めない。** Wayback Machine の 2021-03-20 の保存版で本文を確認した。現行ヘルプに残っている場所は次のとおり。

| 旧記事の項目 | 現行ヘルプでの所在 | 判定 |
|---|---|---|
| 関連性があり、役に立つ独自の内容 | 6238826「役に立つ独自のコンテンツを提供する」「LP が広告とキーワードに一致していることを確認する」、6167130「ユーザーが求めるものを提供」 | ✅ |
| 透明性と信頼性（事業内容・連絡先の明示、個人情報の用途の説明、広告と本文の区別） | 現行の品質スコア関連ヘルプには見当たらない。6238826 に「商品やサービスに関する情報をオープンに」とあるだけ | △（旧記事のみ） |
| 操作のしやすさ | 6238826「LP を操作しやすくする」、広告ランク 1722122「ランディング ページのナビゲーションのしやすさ」、2025-02-05 の公式ブログ | ✅ |
| 読み込み速度 | 6167130「読み込み速度を改善」、7543502 | ✅ |
| モバイル対応 | 6167130、6238826、6167132（デバイスは品質の算出で考慮される） | ✅ |

- ✅ 広告ランクの記事（1722122）は品質の中身を「広告と LP の有用性と関連性、クリックされた広告から抱く LP への期待、LP のナビゲーションのしやすさ、その他の検索品質シグナル」と書く
- ✅ Google の公表（blog.google、2025-02-05）: 広告の品質システムに、LP での移動のしやすさを予測する新しいモデルを入れた。「期待と違うページに着き、ほかへ行く手段もない」広告を減らす。LP は「関連性があり、かつ移動しやすい」ことが必要
- 旧記事にあって今も参考になる一文（2021 年時点、現行では未掲載）: 「特定のものを探している人には特定のページを、選択肢を比べたい人には一般的なページを」

### 1-3.「平均より下」のときに公式が勧める対処 ✅（6167130）

1. ユーザーが求めるものを出す（「フランネル シャツ」で検索→広告→LP もフランネル製品）
2. 広告と LP でメッセージを一貫させる。サイトを変えられなくても、既存ページの中でいちばん合うものを探す
3. コンバージョン率を LP の良し悪しの目安に使う（**ステータスには反映されない**と明記）
4. モバイル対応にする
5. 読み込み速度を改善する

補足 ✅: 検索語句と一字一句同じ文言を LP に載せる必要はない（6167130）。

### 1-4. 評価の更新にかかる時間 △

| 根拠 | 内容 |
|---|---|
| 現行ヘルプ | 更新の所要時間は書かれていない。比較の対象期間は過去 90 日（6167118）。履歴の列を「日」で分けると日ごとの変化が見られる（6167118） |
| 旧記事（2021-03-20 保存版） | 「大きく改善すれば、すぐではないが数日〜数週間で結果が見えることがある」 |
| 6167132 | 新しいキーワードは、関連するキーワード・広告・LP の実績から初期評価が始まり、データが貯まると置き換わる |

推測: 表示回数が少ないキーワードほど、ステータスが動くのに時間がかかる。「直した翌日に変わらない」は失敗の証拠にならない。

出典: support.google.com/google-ads/answer/6167118、6167123、6167130、6167132、156066、1722122、1659694、6238826／web.archive.org の 2404197 保存版／blog.google/products/ads-commerce/search-ads-and-the-importance-of-landing-page-navigation/

## 2. 表示速度と広告の関係

### 2-1. Core Web Vitals の基準値 ✅（web.dev、2024-10-31 更新／PageSpeed Insights の説明、2024-10-21 更新）

| 指標 | 何を測るか | 良好 | 改善が必要 | 不良 |
|---|---|---|---|---|
| LCP | 読み込み（最大の要素が出るまで） | 2.5 秒以下 | 4.0 秒以下 | 4.0 秒超 |
| INP | 操作への反応 | 200 ミリ秒以下 | 500 ミリ秒以下 | 500 ミリ秒超 |
| CLS | 表示のずれ | 0.1 以下 | 0.25 以下 | 0.25 超 |
| FCP（参考、CWV ではない） | 最初の描画 | 1.8 秒以下 | 3.0 秒以下 | 3.0 秒超 |

- 判定は実ユーザーの訪問の 75 パーセンタイルで行う。3 指標すべてが良好で合格
- INP は 2024 年に FID に代わって正式な指標になった。Lighthouse のような実験室ツールは INP を測れず、TBT が代わりの目安
- Lighthouse のスコア: 90 以上が良好、50〜89 が改善が必要、50 未満が不良

### 2-2. 速度が広告の品質評価にどう使われるか

| 公式の記述 | 出典 | 判定 |
|---|---|---|
| LP の利便性が低いときの対処に「読み込み速度を改善」 | 6167130 | ✅ |
| 「モバイル広告の成果を高める簡単な方法のひとつは LP の速度の改善」「小売では、モバイルの読み込みが 1 秒遅れるとモバイルのコンバージョンが最大 20% 減る」 | 7543502 | ✅ |
| 「モバイルサイトの読み込みに 3 秒超かかると訪問の 53% が離脱」（2016 年の Google のデータ） | blog.google、2018-02 | ✅（古い数字） |
| 並行トラッキングは必須。クリック計測を裏で行い、LP へ直接送ることで読み込みを速くする | 7544674 | ✅ |
| Core Web Vitals（LCP・INP・CLS）の値を広告の品質評価に使う、という記述 | — | ❓ 見つからない |
| 速度が何秒なら「平均より下」になる、というしきい値 | — | ❓ 見つからない |

つまり公式に言えるのは「速度は LP の利便性の改善策として挙げられている」まで。「LCP を 2.5 秒にすれば『平均的』に上がる」とは誰も公表していない。第三者の記事には断定するものが多いが、Google の文書を引用していない。

### 2-3.「ランディング ページ」レポート ✅△

- 場所: キャンペーン →［分析情報とレポート］→［ランディング ページ］。「展開後のランディング ページ」も見られる（7543502）
- モバイル対応の列: 「モバイル フレンドリーなページのクリックの割合」（Mobile-friendly click rate）、「有効な AMP ページのクリックの割合」（Valid AMP click rate）。モバイルからのクリックがなければ「—」
- △ ヘルプは今も「Google のモバイル フレンドリー テストで判定」「［テスト］を押すとそのテストが開く」と書くが、モバイル フレンドリー テストは 2023-12-01 に提供終了（Search Central ブログ）。2026-11-16 に旧 URL を開くと Lighthouse の説明ページへ転送された。列が今どう計算されているかは ❓
- ❓ 速度そのものの列（旧「モバイル速度スコア」）は現行ヘルプに記載なし

### 2-4. PageSpeed Insights の実測データと実験室データ ✅

| | 実測データ（フィールド） | 実験室データ（ラボ） |
|---|---|---|
| 元 | Chrome ユーザー エクスペリエンス レポート（CrUX）。実ユーザー、直近 28 日 | Lighthouse。1 台の端末・固定の回線を模擬して 1 回読み込む |
| 端末と回線 | 訪問者それぞれ | モバイルは中位機種（Moto G4）＋モバイル回線を模擬 |
| 出ないとき | ページが新しい、標本が少ない → オリジン単位に切り替え。それでも足りなければ表示なし。noindex（X-Robots-Tag か meta robots）のページ、リダイレクト後に 200 以外を返すページは CrUX の対象外 | 常に出る |
| 注意 | これが Core Web Vitals の合否 | 実行のたびに揺れる。良い点でも実ユーザーが快適とは限らない |

✅ CrUX に実測データが出る条件は公式に 3 つある（developer.chrome.com/docs/crux/methodology）。

1. 公開されていて見つけられる: リダイレクト後に 200 以外を返す、`X-Robots-Tag: noindex` を返す、`<meta name="robots" content="noindex">` を持つページは対象外。広告専用の LP は noindex にしていることが多いので、その場合は人数に関係なく出ない
2. 十分な人気: 最低人数は公開されていない（統計として信頼できる標本数、とだけ書く）
3. 数える利用者: パソコン（Windows・macOS・ChromeOS・Linux）と Android の Chrome で、利用統計の送信と履歴の同期をオンにした人。iOS の Chrome、Android アプリの WebView、Edge などほかの Chromium ブラウザは入らない

訪問が週に数十人のサイトでは実測データが出ない可能性が高い（推測。最低人数は非公開）。その場合、判断材料は実験室データだけになる。計測の側からの説明は 44§7-1 も参照。

出典: web.dev/articles/vitals、web.dev/articles/lab-and-field-data-differences、developers.google.com/speed/docs/insights/v5/about、support.google.com/google-ads/answer/7543502、7544674、developers.google.com/search/blog/2023/04/page-experience-in-search、blog.google/products/ads/speed-scorecard-impact-calculator/

## 3. 同期型の A/B テストタグと表示速度

### 3-1. 何が起きるか ✅（Chrome for Developers、web.dev）

- ブラウザーは HTML を上から読み、同期スクリプトに当たると取得と実行が終わるまで先を読まない。そのスクリプトが `document.write` で別のスクリプトを差し込むと、さらにその取得を待つ。往復の通信が 1 回以上増え、最初の描画が遅れる（Chrome「Intervening against document.write()」、2016）
- 同じ記事の数字（Google の公表、2G 回線の Chrome ユーザー 1% で 28 日間）: 該当スクリプトを止めると、最初の描画までの平均時間が 21% 短縮（1 秒超）、解析完了までが 38% 短縮（約 6 秒）
- Chrome 55 以降の介入: 2G 回線・最上位の文書・解析を止める書き方・別サイトのスクリプト・キャッシュなし・再読み込みでない、の全条件がそろうと、`document.write` で差し込んだスクリプトを**実行しない**
- Lighthouse の「document.write() を使用」の監査: 「ページ表示を数十秒遅らせることがある」「第三者スクリプトの読み込みに使っているなら非同期読み込みに替える」。この監査は Lighthouse 13 で削除済み
- web.dev「LCP を最適化する」（2025-03-31 更新）: LCP が遅れる原因として「`<head>` の同期スクリプトがページ全体の描画を止める」「A/B テストのライブラリがどのテストに入れるか決めるまで要素を隠している」を挙げる

推測（body 直後の同期タグへの当てはめ）: body の先頭で解析が止まるので、本文は 1 文字も描画されない。連鎖の段数ぶん、名前解決・接続・取得が直列に積み上がり、FCP と LCP がその分そのまま遅れる。2G 相当の回線では Chrome の介入でタグ自体が動かず、その訪問はテストに入らない可能性がある。

### 3-2. 各社と Chrome の推奨（誰の主張かを明記）

| 誰 | 推奨 | 理由・数字 |
|---|---|---|
| Chrome for Developers（2026-07-09 更新） | `<head>` に `async` と `blocking="render"` を付けて読む。重い処理は分ける。速度が大事なページはサーバー側で出し分ける | 従来のちらつき防止は全体を透明にして待つ方式で、時間切れは典型的に 4 秒。段階的な描画を捨てる。`blocking="render"` 非対応のブラウザーだけ従来方式で補う |
| Optimizely | `<head>` に同期で置く。非同期（async／defer）は「最後の手段」 | 非同期はちらつきを生み、結果の信頼性を下げる。CDN で影響を抑えると主張 |
| AB Tasty | 多くの場合は同期タグを `<head>` に。タグは 120kb 未満に保つ | 同期は読み終わるまで描画を止めるので、ちらつきが減る |
| Kameleoon | **非同期タグ＋ちらつき防止**を推奨。同期タグは「推奨しない」 | 隠す時間の上限は既定 750 ミリ秒。通常 50 ミリ秒未満で解除。時間切れは訪問者の 2〜3% 程度と主張。「止める形で資源を読むのは良い作法ではない」 |
| VWO（ヘルプ上の社名表記は Wingify） | 非同期の SmartCode。ページの上部に置く | ページと並行して読む。時間切れなら元のページを出す |
| DebugBear（第三者の検証） | サーバー側で出し分ける／一部だけ隠す／読み込みを速くする／ちらつきを受け入れる | ある実サイトで、隠す処理を外すと LCP が 6.0 秒→2.7 秒になった例 |

### 3-3. トレードオフの整理

| 方式 | ちらつき | 表示速度 | 計測の欠け |
|---|---|---|---|
| 同期（`<head>` や body 直後） | 出にくい | タグの取得・実行ぶん必ず遅れる。連鎖があれば段数ぶん遅れる | 少ない（遅い回線では離脱が増える） |
| 非同期＋ちらつき防止 | 出にくい（時間切れ時は出る） | 隠している間は白い画面。LCP は遅れる。上限を短くできる | 時間切れの訪問は元のページを見る |
| 非同期のみ | 出る。途中で内容が入れ替わり CLS も悪化しうる | 速い | 入れ替わりを見た人の行動がゆがむ |
| サーバー側 | 出ない | 影響なし | なし。実装の手間が大きい |

どの方式でも共通（各社の一致点）: タグは軽く、できるだけ上に、タグ マネージャー経由にしない、テストしていないページには読ませない。

出典: developer.chrome.com/blog/removing-document-write、developer.chrome.com/docs/lighthouse/best-practices/no-document-write、web.dev/articles/optimize-lcp、developer.chrome.com/docs/modern-web-guidance/guides/performance/flicker-free-client-side-ab-testing、各社ドキュメント（出典一覧）

## 4. 検索の意図と LP の内容の一致

### 4-1. 公式の根拠 ✅

- LP は広告とキーワードに一致させる。それが「広告の関連性」と「LP の利便性」を高める（6238826）
- 広告で約束したこと（特典、行動を促すフレーズ）を LP が受け止める（6167130、6238826）
- 重要な情報はページの上部に（6238826）
- ユーザーの意図との関連性が広告の品質の中心。点数の操作でなく、検索に答えることに集中する（6167132）
- 期待と違うページに着いたとき、良い体験かどうかは「そこから目的の場所へどれだけ早く行けるか」で決まる（blog.google、2025-02-05）

### 4-2. 同じページに足すか、別ページを作るか

公式はどちらが良いとは書いていない（❓）。確認できた事実と推測を分ける。

| 観点 | 同じページに足す | 別ページを作る | 根拠 |
|---|---|---|---|
| LP の利便性の評価 | そのキーワードの着地先は同じ。中身が検索意図に近づけば改善しうる | そのキーワードの広告の最終ページ URL を新ページにすれば、評価される着地先が変わる | 推測（評価はキーワード単位で、着地先との関連性を見る、という公式記述から） |
| 広告の関連性 | 影響なし | 影響なし（広告文とキーワードの関係。LP を変えるだけでは動かない） | ✅ 6167130 |
| 最終ページ URL の拡張（FUE） | 候補は増えない | 新ページが候補になりうる。FUE は「検索語句に関連し、広告グループのテーマに沿う、ドメイン内の URL」へ送る | ✅ 16230205（新ページが実際に選ばれるかは ❓） |
| テキストのカスタマイズ | ページ内容が広告文の材料になる | 新ページの内容も材料になる | ✅ AI Max メモ 3 章 |
| 移動のしやすさ | ページ内で完結 | ページ間のリンクが分かりやすいことが条件 | ✅ blog.google 2025-02-05 |
| 実行中の A/B テストへの影響 | テスト中の画面を変えると、前後のデータを混ぜられない | テスト対象のページは変えずに済む。ただし FUE や URL 変更で流入が減る | 推測 |

第三者の主張: 「メッセージ マッチで成果が上がる」という記事は多いが、今回取得した範囲で、条件をそろえた公開の検証は確認できなかった（❓）。公式の根拠だけで足りるので、このメモでは引用しない。

出典: support.google.com/google-ads/answer/6238826、6167130、6167132、16230205

## 5. A/B テストの統計

### 5-1. 必要なサンプル数 ✅（Kohavi ら）

| 出典 | 式 | 前提 |
|---|---|---|
| Kohavi・Henne・Sommerfield（KDD 2007） | n =（4rσ/Δ）²。n は全体、r は群の数 | 信頼水準 95%、検出力 90%。ただし保守的な目安: 成果率 5%・相対 20% の差（2 群）では全体 30,400 で、標準の式（両側 5%・検出力 90%、分散 p(1−p)）の全体 約 20,000 より大きく出る【検算】 |
| Kohavi・Deng・Vermeer（KDD 2022） | 1 群あたり n = 16σ²/δ² | 有意水準 5%、検出力 80%（係数を保守的に 16 へ丸めた） |

- σ² は成果率 p のとき p(1−p)。Δ（δ）は検出したい差の絶対値
- 決める順番: 今の成果率 → 検出したい最小の差 → 検出力 → 必要数 → 期間
- 2007 年論文の例: 成果率 5% で相対 5% の差を見るには 50 万人弱。相対 20% なら 30,400 人
- 同論文: 50%/50% の配分が検出力を最大にする（99%/1% は約 25 倍の期間がかかる）。曜日の影響があるので最低 1〜2 週間、以後は週の倍数で回す
- 2022 年論文: 1 群 80 人・成果率 3.7% のテストは、相対 10% の差に対する検出力が 3%。検出力が 0.1 を下回ると、効果の向きを取り違える確率が 50% に近づく。A/B テストが役に立つのは「少なくとも数千人、できれば数万人」いるとき。検出力が低いテストで出た有意な結果は、効果を大きく見積もりすぎる
- 書籍 "Trustworthy Online Controlled Experiments"（Kohavi・Tang・Xu、2020）は書誌だけ確認。本文は未取得（❓）

### 5-2. 途中で結果をのぞく問題（peeking）✅

- Evan Miller（2010）: 有意差の計算は「標本数を先に決めた」ことが前提。「有意になったら止める」をやると偽陽性が増える。毎回検定して最大 150 件で止める場合、差がないのに有意と出る確率は 26.1%。10 回のぞくなら、表示上 1.0% で初めて実質 5%
- Johari・Koomen・Pekelis・Walsh（arXiv 1512.04922、関連論文 "Peeking at A/B Tests" は KDD 2017）: 利用者が見ながら止め時を決めると、p 値と信頼区間は「まったく当てにならない」。いつ見ても有効な p 値（always valid）を定義。大規模な商用の A/B テスト基盤に実装済みと記載。KDD 2017 版は書誌のみ確認（本文は取得不可）

### 5-3. 逐次検定 ✅

- Evan Miller（2015）の簡易な方法: 先に N を決める。処置群の成果数 T と対照群の成果数 C を数え、T−C が 2√N に達したら処置群の勝ち、T+C が N に達したら勝者なしで終了。成果率が低いほど向く
- Optimizely（公式ブログ、2026-05-13）: 同社の統計エンジンは逐次検定（mSPRT）。いつ見ても偽陽性率が保たれ、検出したい差を先に決めなくてよい、と同社は主張。検出したい差が小さいときは固定期間方式よりやや多くの標本が要ることがある、とも書く

### 5-4. ベイズの「勝つ確率」の読み方

- 意味（Optimizely の説明）: 頻度論はデータに確率を付け、ベイズは仮説に確率を付ける。「B が対照より良い確率 90%」
- 「のぞいても大丈夫」は言いすぎ（第三者 David Robinson の検証、Variance Explained）: 同じ判断基準で、最後だけ見ると誤って B に替えるのは 2.5%、毎日のぞいて止めると 11.8%。ベイズ法が約束しているのは偽陽性率ではなく期待損失の上限
- △ Optimizely の同じ記事は「ベイズは同じ偽陽性の制約がなく、途中で見ても妥当性を損なわない」と書く。上の検証と食い違う。ツールの表示は「のぞいて止めたら甘くなる」前提で読む
- 少ないデータでは事前分布がそのまま出る（このメモの計算、一様な事前分布）: 0/40 対 0/40 → 50%。0/40 対 1/40 → 約 75%。0/40 対 2/40 → 約 88%。**成果 1〜2 件で「勝つ確率」が 25〜38 ポイント動く。**同じ 0/40 対 2/40 を頻度論（フィッシャーの正確検定）で見ると p ≈ 0.49 で、差があるとは言えない
- VWO（Wingify）のヘルプ: 対照かパターンの成果率が 0 のとき、また訪問者数がしきい値未満のとき、勝つ見込み（chance to beat）は計算しない（多変量テスト向けの頻度論の計算の説明）

### 5-5. 多腕バンディット ✅（各社の説明）

- Optimizely: 「バンディットは最適化のためのもので、実験のためのものではない」「統計的有意性は出さない」。見出しのテストや短期の販促向け。仮説の検証には使わない
- VWO: 統計的有意性より、短期間で指標を最大化したい人向け
- 推測: 成果がほぼ 0 件なら、配分を寄せる材料がないのでバンディットも働かない。「少ない訪問数の解決策」にはならない

### 5-6. 少ない訪問数（1 日数人〜数十人）で言えること・言えないこと

このメモの計算（両側 5%、検出力 80%、2 群の比率の差の標準的な式）:

| 今の成果率 → 検出したい成果率 | 1 群あたり | 合計 | 週 30〜80 人なら |
|---|---|---|---|
| 5% → 6%（相対 +20%） | 約 8,200 | 約 16,300 | 204〜544 週（4〜10 年） |
| 2% → 4%（2 倍） | 約 1,100 | 約 2,300 | 29〜76 週 |
| 5% → 10%（2 倍） | 約 430 | 約 870 | 11〜29 週 |
| 10% → 20%（2 倍） | 約 200 | 約 400 | 5〜13 週 |
| 20% → 30%（1.5 倍） | 約 290 | 約 590 | 7〜20 週 |

| 言えること | 言えないこと |
|---|---|
| 成果 0 件のとき、成果率の上限の目安。n 人で 0 件なら、95% の信頼で 3/n 以下（3 の法則）。40 人なら約 7.5% 以下、80 人なら約 3.8% 以下 | A と B のどちらが良いか。両方 0 件なら情報はゼロ |
| 計測が動いているか、表示が崩れていないか、極端な事故がないか | 見出しの文言のような小さな差の優劣 |
| 成果率が 2 倍になるような大きな差なら、数か月かければ見える可能性 | 1〜2 件の差から出た「勝つ確率 88%」の意味づけ |

補足（このメモの計算）: 本当の成果率が 2% でも、40 人で 0 件になる確率は 45%。5% でも 13%。1 群 40 人で 5%→10% を見つけられる検出力は約 14%。

出典: exp-platform.com の 2 論文、evanmiller.org の 2 記事、arxiv.org/abs/1512.04922、optimizely.com のブログとドキュメント、varianceexplained.org、help.vwo.com、vwo.com、en.wikipedia.org の Rule of three

## 6. Google 広告側のテスト機能 ✅

入口は［テスト］ページ（旧称: 下書きとテスト。英語 Experiments）。

| 種類（日本語ヘルプ／英語） | 何を試すか | 仕組みと制限 |
|---|---|---|
| カスタムテスト／Custom experiments | スマート自動入札、マッチタイプ、LP、オーディエンス、広告グループ | 元のキャンペーンから試験用を作り、予算と流入を分ける。検索は Cookie ベースか検索ベースで分割。予約は 5 件まで、同時に動くのは 1 件。元のキャンペーンの変更は試験側に反映されない。Cookie 分割でオーディエンス リストを使うなら 10,000 人以上 |
| 広告バリエーション／Ad variations | 広告文の 1 つの変更を複数キャンペーンやアカウント全体で | レスポンシブ検索広告が対象。流入の割合と終了日を決める |
| AI 最大化設定テスト／AI Max experiments | AI Max のオン・オフ | 複製せず、1 つのキャンペーンの中で分ける（［テスト］ページの説明では 50/50）。試験側は既定でテキストのカスタマイズ・FUE・検索語句のマッチングがオン。テキストのカスタマイズが既にオン、ディスプレイ ネットワーク対象、ポートフォリオ入札、共有予算、入札の探索（Bidding exploration）、実行中のテストあり、の場合は作れない。適用せず終えると元に戻る |

- 結果の見方（6318747）: 差の推定値と信頼区間が出る。信頼区間の既定は 80%（変更可）。統計的に有意なら青いアスタリスク。有意でない理由として「期間が短い」「流入が少ない」「分割が小さい」「差がない」を挙げる
- △ 勝者が決まらないときの推奨期間: 6318747 は「2〜3 週間」、10682377 は「少なくとも 4〜6 週間」
- ✅ 好結果の自動適用は、一部のテストで既定でオン（6318747）。勝者の決め方は「方向性のある結果」か「統計的有意性を必須」から選ぶ（6261395）
- ✅ テスト中に元のキャンペーンや試験側を変更すると、結果の解釈が難しくなる（6261395）

### LP の A/B テストと広告のテストを同時に走らせるときの注意（公式の記載なし ❓、以下は推測）

- 広告側の分割と LP 側の分割が独立なら、理屈の上では互いを偏らせない。ただし比べる単位ごとの人数がさらに半分になる
- AI Max の試験側は FUE で着地先を変える。LP の A/B テストが 1 ページだけなら、試験側の流入はテストの外へ出る
- テキストのカスタマイズは LP の内容を広告文の材料にする。LP の出し分けのどちらを Google が読むかは不明
- 流入が少ないなら、同時にやらず 1 つずつ
- 「少ない」の線（推測。式は 5-1 の Kohavi・Deng・Vermeer 2022、1 群 n ＝ 16σ²/δ²、σ² ＝ p(1−p)）: 広告側 2 群 × LP 側 2 群の 4 群それぞれで必要な人数が、待てる期間（このメモの目安は 8 週間、§10）の中に集まるなら同時でよい。例: 成果率 5%・相対 20% の差（δ ＝ 1 ポイント）なら 1 群 7,600 人、4 群で 30,400 人。8 週間なら LP の訪問が週 3,800 人以上。届かなければ 1 つずつ（43 §8-1 と同じ条件）

出典: support.google.com/google-ads/answer/10682377、6261395、7438541、16450159、6318747

## 7. LP の A/B テストと Google のクローラー

| 項目 | 内容 | 出典・判定 |
|---|---|---|
| クローキングにしない | Googlebot と人に別の URL 群を見せない。サーバー側の振り分けや robots.txt でやっても同じ | ✅ Search Central（英語版 2025-12-10 更新、日本語版の表示は 2026-11-10） |
| 複数 URL でテストするとき | 代替 URL に `rel="canonical"` で元の URL を示す。noindex は使わない | ✅ 同上 |
| 転送するとき | 301 でなく 302。JavaScript の転送も可 | ✅ 同上 |
| 期間 | 必要な期間だけ。終わったらテスト用の要素をすぐ外す。長すぎると欺く意図とみなすことがある | ✅ 同上 |
| Cookie | Googlebot は通常 Cookie を使わない。Cookie なしで見える版だけを見る | ✅ 同上 |
| 小さな変更 | ボタンや画像、行動を促す文言の変更は、検索結果のスニペットや順位にほとんど影響しない | ✅ 同上 |
| 広告のポリシー | クローキング＝ポリシー違反を隠すために、人や Google に違う内容を見せること。言語・特典・地域・回線速度に応じて少し違う内容を出すのは可。宣伝する商品やサービスは全員に同じであること | ✅ 広告ポリシー 15938075 |
| 広告のクローラー | AdsBot は robots.txt の全体指定（*）を無視する。止めるには名指しが必要。AdsBot への指定は「広告の品質の確認」に影響する。リンク先はクロールできることが要件 | ✅ クローラー一覧（2026-09-17 更新）、6368661 |
| AdsBot が JavaScript の出し分けのどちらを見るか | 記載なし | ❓ |

出典: developers.google.com/search/docs/crawling-indexing/website-testing、developers.google.com/search/docs/crawling-indexing/google-special-case-crawlers、support.google.com/adspolicy/answer/15938075、6368661

## 8. LPO ツールの成果と、広告側の成果がずれる理由

| ずれの元 | Google 広告 | LPO ツール・解析ツール | 判定 |
|---|---|---|---|
| 計上する日 | メインの列は**クリックの日時**で計上 | 成果が起きた日時 | ✅ 6270625 |
| 数える単位 | クリック。同じ人が 2 回押せば 2 | 訪問者やセッション | ✅ 1034383（旧 UA 向けの記事） |
| 無効なクリック | 自動で除く | 全部数える | ✅ 同上 |
| カウント方法 | 「全件」か「初回のみ」を成果ごとに設定 | ツールごと | ✅ 3438531 |
| 計測期間 | 既定 30 日。広告との接点から数える | ツールごと | ✅ 3123169 |
| 流入元 | 広告経由だけ | 自然検索や直接流入も含む（絞らない限り） | 推測 |
| 着地ページ | FUE で別ページに着いた人も数える | テスト対象ページに来た人だけ | 推測 |
| タグが動く前の離脱 | クリックは数える | タグが読まれなければ訪問も成果も数えない | 推測 |
| 推定の補完 | 推定コンバージョンを含むことがある | 含まない | ✅ 6270625 |

- ✅ 公式の目安（6270625）: 第三者の解析と比べるときは「すべてのコンバージョン（コンバージョンの日時別）」を使う。それでも最大 20% 程度の差は起こりうる
- 少量のアカウントでは、この 20% の揺れのほうが A/B の差より大きい（推測）

出典: support.google.com/google-ads/answer/6270625、3438531、3123169／support.google.com/analytics/answer/1034383、13965727

## 9. よくある思い込みと実際

| 思い込み | 実際 | 出典 |
|---|---|---|
| 品質スコアはオークションで使われる | 使われない。診断用。使われるのは 3 要素のリアルタイム評価 | 6167118、6167123 |
| LP の利便性の 5 項目は現行ヘルプに載っている | 元の記事は転送済みで読めない。「透明性と信頼性」は現行の品質スコア関連ヘルプにない | 2404197 の保存版 |
| コンバージョン率が上がれば LP の評価も上がる | 目安にはなるが、ステータスには反映されない。記録された成果の件数も品質に影響しない | 6167130、156066 |
| 検索語句と同じ言葉を LP に入れないと評価されない | 一字一句同じ文言は不要 | 6167130 |
| Core Web Vitals が広告の品質評価に使われると Google が明言している | そういう記述は見つからない。速度の改善は勧めている | 2-2 節 |
| Lighthouse の点数＝実ユーザーの体験 | 実験室の 1 回の測定。合否は実測の 75 パーセンタイル | PSI の説明 |
| A/B テストのタグは非同期にすれば解決 | ちらつきが出る。防止策を入れると白い画面で待たせる。各社の推奨も割れている | 3-2 節 |
| 管理画面で「95%」と出たら止めてよい | 標本数を先に決めていなければ偽陽性が膨らむ（最大 26.1% の例） | Evan Miller |
| ベイズならいつ止めても大丈夫 | 止め方しだいで誤判断は 4 倍超に増える | Robinson（第三者） |
| 少ない訪問数ならバンディットで | 有意性は出ない。検証ではなく最適化の道具 | Optimizely |
| A/B テストはクローキングになる | 人とクローラーに同じ仕組みで出していればならない。長く続けすぎない | Search Central |
| robots.txt の * で AdsBot も止まる | 止まらない。名指しが必要 | クローラー一覧 |

## 10. 少額・少量のアカウントでの注意

- 品質スコアは完全一致の検索が足りないと「—」。出ていても動きは遅い（推測）
- 実測の速度データ（CrUX）が出ない可能性が高い。Lighthouse を条件をそろえて複数回走らせ、中央値で見る
- 2 群に分けると 1 群の人数は半分。A/B テストの前に 5-6 節の表で期間を出す。8 週間を超えるなら A/B テストをしない（8 週間はこのメモの目安。公式の基準ではない）
- 成果が月に数件なら、1〜2 件の偶然で結論が逆になる。「勝つ確率」も同じ
- 広告側のテストの自動適用は、勝者の決め方を「統計的有意性を必須」にするか、オフにする
- 広告のテストと LP のテストを同時に走らせない（少量のアカウントでの注意。4 群それぞれの人数が 8 週間で集まるほど流入が多いなら同時でよい、という 43 §8-1 と同じ条件の裏返し。線の出し方は §6 の「同時に走らせるときの注意」）
- LPO ツールと広告の数字の差（最大 20% 程度）は誤差として扱う
- 同期タグは、テストの結論が出ない間も全訪問者の表示を遅らせる。結論が出ないテストは、費用だけがかかる

## 11. 判断の型

1. 成果は測れているか。自分で押して、LPO ツールと広告の両方に 1 件入るかを先に確かめる
2. キーワードごとに意図を分ける。「広く知りたい」か「特定のものを知りたい」か。意図と着地先が合っているかを見る
3. 合っていなければ、内容か着地先を直す。ここは A/B テストにかけず、直す
4. 速度を測る。タグあり・なしで比べ、タグの重さを数字にする。LCP 2.5 秒以下を目標にする
5. A/B テストをやるなら、先に必要数と期間を計算し、止める条件を決めてから始める。途中でのぞいて止めない
6. 期間が現実的でなければ、大きな変更を 1 つずつ入れ、前後を記録する。前後比較は因果の証明にはならないと割り切る
7. 品質スコアは履歴の列を日別で記録する。数日〜数週間は待つ
8. テストは一度に 1 つ

## 12. この状況への当てはめ

前提: 3 ページの情報サイト。主力は競技名そのもの（月 49,500）と「競技名 ルール」。LP の利便性が 4 語中 3 語で「平均より下」。スマホの Lighthouse 67 点、FCP 4.1 秒、LCP 5.7 秒（2026-11-10 計測）。body 直後に LPO ツールの同期タグ。トップのファーストビューを A 50%・B 50% で出し分け中。訪問は週 30〜80 人、成果は実質 0 件。

### 何から直すと評価に効きそうか（順番は推測。効果の保証はどこにもない）

1. **意図の不一致を先に直す。** 競技名だけで検索する人は競技全般を知りたい可能性が高いのに、着地先は 6人制の解説が中心。公式が「平均より下」の対処の 1 番目に挙げる「ユーザーが求めるものを出す」に当たる。別ページ「競技とは」（発祥・人数・ポジション・10人制との違い）を作り、競技名と「競技名 ルール」の広告の最終ページ URL をそこへ向ける案は筋が良い。理由は 3 つ。評価される着地先そのものを替えられる。FUE の候補にもなりうる。テスト中のトップページを触らずに済む。条件は、新ページと 6人制のページを互いに分かりやすくつなぐこと（2025-02 の移動のしやすさのモデル）
2. **速度。** FCP 4.1 秒・LCP 5.7 秒は、基準値に当てはめるとどちらも「不良」の範囲。まず同期タグを止めた状態で Lighthouse を取り直し、タグの分を数字にする。大きければ、テスト対象でない 2 ページからタグを外す、非同期＋短い時間切れのちらつき防止に替えられるかを LPO ツールの提供元に確認する、のどちらか。速度が評価に効くかは未確認だが、離脱を減らす点では公式も勧めている
3. **モバイル対応と操作。**「ランディング ページ」レポートの「モバイル フレンドリーなページのクリックの割合」が 100% かを見る
4. 直したら、4 語のステータスを履歴の列で日別に記録し、数週間は待つ

### 今の訪問数で A/B テストから言えること

- **A と B の優劣は何も言えない。** 1 群は週 15〜40 人で、成果は両方 0 件。成果率が 5% から 10% に倍増するほどの差でも、見つけるには合計約 870 人（11〜29 週）要る。見出しの文言で起きがちな相対 20% の差なら 4〜10 年
- **言えるのは「ボタンがほぼ押されていない」こと。** 累計 n 人で 0 件なら成果率は 95% の信頼で 3/n 以下。4 週間で 120〜320 人なら、およそ 1〜2.5% 以下。文言の出し分けより前に、ボタンの位置・意味・計測のどれかに問題がある可能性が高い。計測の動作確認が最優先
- **このテストは止めるか、問いを変える。** 続けても結論は出ず、同期タグが全訪問者の表示を遅らせ続ける。選択肢は 3 つ。(a) テストを止めてタグを外し、1 と 2 を直す。(b) スクロール到達やページ内リンクのクリックのような、もっと起きやすい行動を成果にして必要数を計算し直す（ただし答える問いが変わる）。(c) 出し分けをやめ、大きな変更を 1 つずつ入れて前後を記録する
- 広告側で AI Max のテストを足すなら、LP のテストを終えてからにする

## 13. 未確認の点と確かめ方

| 未確認の点 | 確かめ方 |
|---|---|
| ❓ Core Web Vitals や速度の具体的な値が LP の利便性に使われるか | 公式の記載なし。タグを外して速度を改善した前後で、同じキーワードのステータスを履歴の列で日別に追う（1 サイトの前後比較で、因果は言えない） |
| △ 評価の更新にかかる時間 | 現行ヘルプに記載なし。変更日を記録し、履歴の列で何日後に動いたかを見る |
| △「透明性と信頼性」が今も評価項目か | 現行ヘルプにない。運営者情報・連絡先・プライバシーの説明を載せるのは低コストなので、やっておく |
| △「モバイル フレンドリーなページのクリックの割合」の今の算出方法 | 管理画面で列の説明と［テスト］の行き先を見る |
| ❓ AdsBot が JavaScript の出し分けのどちらを見るか | サーバーのログで AdsBot-Google のアクセスを探し、LPO ツール側でその訪問がどちらに入ったかを見る |
| ❓ 同期タグが FCP・LCP を何秒遅らせているか | Chrome の開発者ツールでタグのドメインを止めて Lighthouse を 5 回ずつ取り、中央値を比べる |
| ❓ 新ページが FUE で実際に選ばれるか | AI Max メモ 2 章のとおり、「ランディング ページ」レポートの「選択元」列で見る |
| ❓ LP と広告のテストを同時に走らせたときの公式の扱い | 記載なし。同時にやらない |
| ❓ 使っている LPO ツールの統計の方式、非同期タグやちらつき防止の有無 | 提供元の公開ドキュメントは今回見つからなかった。提供元に聞く |
| ❓ Kohavi らの書籍、Johari らの KDD 2017 版の本文 | 書誌のみ確認。本を読む |
| ❓ メッセージ マッチの効果を条件をそろえて測った公開の検証 | 今回は見つからなかった |

## 14. 出典一覧（取得日はすべて 2026-11-16）

### Google 公式（Google 広告ヘルプ。日本語版 `?hl=ja` と英語版の両方を取得）
- 検索キャンペーンの品質スコアについて: https://support.google.com/google-ads/answer/6167118
- 改善の指針としての品質スコア: https://support.google.com/google-ads/answer/6167123
- 品質スコアを使って広告のパフォーマンスを高める 5 つの方法: https://support.google.com/google-ads/answer/6167130
- 広告の品質向上において重要な点とそうでない点: https://support.google.com/google-ads/answer/6167132
- 広告の品質について: https://support.google.com/google-ads/answer/156066
- 広告ランクについて: https://support.google.com/google-ads/answer/1722122
- ランディング ページの利便性（用語集）: https://support.google.com/google-ads/answer/1659694
- 広告とランディング ページを最適化する: https://support.google.com/google-ads/answer/6238826
- ランディング ページのパフォーマンスを評価する: https://support.google.com/google-ads/answer/7543502
- 並行トラッキングについて: https://support.google.com/google-ads/answer/7544674
- 旧記事 Understanding landing page experience（現在は 6167118 へ転送）: https://support.google.com/google-ads/answer/2404197 ／ 保存版（2021-03-20）: https://web.archive.org/web/20210320022132/https://support.google.com/google-ads/answer/2404197
- ［テスト］ページ（旧称: 下書きとテスト）について: https://support.google.com/google-ads/answer/10682377
- カスタムテストを設定する: https://support.google.com/google-ads/answer/6261395
- 広告バリエーションについて: https://support.google.com/google-ads/answer/7438541
- AI 最大化設定テストについて: https://support.google.com/google-ads/answer/16450159
- Monitor your experiments: https://support.google.com/google-ads/answer/6318747
- About Final URL expansion in Search: https://support.google.com/google-ads/answer/16230205
- コンバージョン トラッキング データについて: https://support.google.com/google-ads/answer/6270625
- About conversion counting options: https://support.google.com/google-ads/answer/3438531
- About conversion windows: https://support.google.com/google-ads/answer/3123169
- 広告ポリシー Circumventing systems: https://support.google.com/adspolicy/answer/15938075
- 広告ポリシー Destination requirements: https://support.google.com/adspolicy/answer/6368661
- アナリティクス ヘルプ Conversions vs. key events: https://support.google.com/analytics/answer/13965727
- アナリティクス ヘルプ [UA] Data discrepancies between Google Ads and Analytics（旧版）: https://support.google.com/analytics/answer/1034383

### Google 公式（ブログ、Search Central、web.dev、Chrome）
- Search ads and the importance of landing page navigation（2025-02-05）: https://blog.google/products/ads-commerce/search-ads-and-the-importance-of-landing-page-navigation/
- Introducing the mobile Speed Scorecard and Impact Calculator（2018）: https://blog.google/products/ads/speed-scorecard-impact-calculator/
- A/B テスト時に Google 検索への影響を最小限に抑える: https://developers.google.com/search/docs/crawling-indexing/website-testing
- Google の特殊なクローラー一覧: https://developers.google.com/search/docs/crawling-indexing/google-special-case-crawlers
- ページ エクスペリエンスに関するブログ（モバイル フレンドリー テストの終了を告知、2023-04）: https://developers.google.com/search/blog/2023/04/page-experience-in-search
- About PageSpeed Insights: https://developers.google.com/speed/docs/insights/v5/about
- Web Vitals: https://web.dev/articles/vitals
- Why lab and field data can be different: https://web.dev/articles/lab-and-field-data-differences
- Optimize Largest Contentful Paint: https://web.dev/articles/optimize-lcp
- Intervening against document.write()（2016）: https://developer.chrome.com/blog/removing-document-write
- Lighthouse: Uses document.write(): https://developer.chrome.com/docs/lighthouse/best-practices/no-document-write
- Flicker-Free Client-Side A/B Testing（2026-07-09 更新）: https://developer.chrome.com/docs/modern-web-guidance/guides/performance/flicker-free-client-side-ab-testing

### 論文・統計の解説
- Kohavi, Henne, Sommerfield "Practical Guide to Controlled Experiments on the Web"（KDD 2007、著者版）: https://www.exp-platform.com/Documents/GuideControlledExperiments.pdf
- Kohavi, Deng, Vermeer "A/B Testing Intuition Busters"（KDD 2022、DOI 10.1145/3534678.3539160）: https://exp-platform.com/abtestingintuitionbusters/ ／ 著者版 PDF: https://airbnb.tech/wp-content/uploads/sites/19/2025/10/ABTestingIntuitionBusters.pdf
- Kohavi, Tang, Xu "Trustworthy Online Controlled Experiments"（Cambridge University Press、2020。書誌のみ）: https://experimentguide.com/
- Johari, Pekelis, Walsh "Always Valid Inference: Bringing Sequential Analysis to A/B Testing": https://arxiv.org/abs/1512.04922
- Johari, Koomen, Pekelis, Walsh "Peeking at A/B Tests"（KDD 2017、DOI 10.1145/3097983.3097992。書誌のみ）: https://dl.acm.org/doi/10.1145/3097983.3097992
- Evan Miller "How Not To Run an A/B Test"（2010）: https://www.evanmiller.org/how-not-to-run-an-ab-test.html
- Evan Miller "Simple Sequential A/B Testing"（2015）: https://www.evanmiller.org/sequential-ab-testing.html
- David Robinson "Is Bayesian A/B Testing Immune to Peeking? Not Exactly": http://varianceexplained.org/r/bayesian-ab-testing/
- Rule of three (statistics): https://en.wikipedia.org/wiki/Rule_of_three_(statistics)

### A/B テストツール各社・第三者
- Optimizely「Load snippet synchronously and asynchronously」: https://support.optimizely.com/hc/en-us/articles/4410289847053
- Optimizely「Install the snippet as a non-blocking resource」: https://support.optimizely.com/hc/en-us/articles/4410289790221-Install-the-snippet-as-a-non-blocking-resource
- Optimizely「Page load speed」: https://docs.developers.optimizely.com/web-experimentation/docs/page-load-speed
- Optimizely「how to choose the right stats engine」（2026-05-13）: https://www.optimizely.com/insights/blog/how-to-choose-the-right-stats-engine/
- Optimizely「Run a multi-armed bandit optimization in Feature Experimentation」: https://docs.optimizely.com/feature-experimentation/docs/run-a-multi-armed-bandit-optimization-in-feature-experimentation
- AB Tasty「Choose between synchronous and asynchronous methods」: https://docs.abtasty.com/account/tag-integration/understand-ab-tasty-tag-implementation-methods/choose-between-synchronous-and-asynchronous-methods
- AB Tasty「[Troubleshooting] flickering」: https://docs.abtasty.com/help-center/troubleshooting/troubleshooting-i-see-the-original-version-very-briefly-before-i-see-the-variation-flickering
- Kameleoon「Flicker management and performance」: https://docs.kameleoon.com/developer-docs/web-experimentation/technical-concepts/flicker-management-and-performance
- Kameleoon「Standard implementation」: https://docs.kameleoon.com/developer-docs/web-experimentation/implementation-and-deployment/standard-implementation
- VWO（Wingify）「Advantages of Asynchronous SmartCode」: https://help.vwo.com/hc/en-us/articles/360019422174-Advantages-of-VWO-Asynchronous-SmartCode
- VWO（Wingify）「Why Do I Notice a Page Flicker」: https://help.vwo.com/hc/en-us/articles/360020440754
- VWO（Wingify）勝つ見込みの計算: https://help.vwo.com/hc/en-us/articles/360033991053
- VWO「Multi-Armed Bandit」用語集: https://vwo.com/glossary/multi-armed-bandit-testing/
- DebugBear「Anti-Flicker Snippets From A/B Testing Tools And Page Speed」: https://www.debugbear.com/blog/ab-testing-anti-flicker-body-hiding

### 取得できなかったもの
- dl.acm.org（403）、searchengineland.com（403）、Google アナリティクスの旧バンディット解説 answer/2844870（404）、Kohavi らの 2009 年サーベイ（接続不可）

## 15. 変更の記録

- 2026-11-16: 初版
- 2026-11-17 裏取り: §6 の信頼区間の既定 80%・自動適用・有意でない理由（6318747）、勝者が出ないときの推奨 2〜3 週間と 4〜6 週間の食い違い（6318747・10682377）、予約 5・同時 1（6261395）は一致を確認（直さず）。§8 の最大 20% の差（6270625）も一致
- 2026-11-17 後始末: §2-4 の表「出ないとき」に「noindex（X-Robots-Tag か meta robots）のページ、リダイレクト後に 200 以外を返すページは CrUX の対象外」を追加。表の下「（推測。公式は『標本が少ないと出ない』とだけ書く）」→ CrUX の公式の 3 条件（公開・発見可能、十分な人気＝最低人数は非公開、PC・Android の Chrome の利用者。iOS の Chrome・WebView・Edge などは入らない）の番号付きの説明を足し、推測の文を「（推測。最低人数は非公開）」に直し、44§7-1 への参照を足した → 理由: LP の裏取り担当の提案（94_裏取りの記録_41-44.md 3 章、44 の担当から）。methodology のページに 200 以外・X-Robots-Tag: noindex・meta robots noindex の除外、「An exact number is not disclosed」、対象の OS と除外のブラウザがあることを確かめた → 出典: https://developer.chrome.com/docs/crux/methodology （2026-11-17 取得）
- 2026-11-17 後始末: §5-1 の表の Kohavi 2007 の式に「保守的な目安（5%・相対 20% で全体 30,400、標準の式の検出力 90% では約 20,000）」を注記 → 理由: 理解度テストの指摘（93 の LP-5）。式の前提を「検出力 90%」とだけ書くと、標準の式で出した数と合わず読み手が迷う。Python で検算した（σ²＝0.05×0.95、Δ＝0.01） → 出典: 既存の出典（Kohavi・Henne・Sommerfield 2007 著者版）のまま。数字はこのメモの検算
- 2026-11-17 後始末 2: §6「同時に走らせるときの注意」に「少ない」の線（4 群それぞれの必要人数が 8 週間で集まるか、5%・相対 20% なら週 3,800 人）を足し、§10「同時に走らせない」に条件と 43 §8-1 との関係を書いた → 理由: 07 §10（同時にしない）と 43 §8-1（流入が多ければ同時でよい）の条件の違いが明示されていなかった（90_核となる理解 §13 の 6）。式は 5-1 の Kohavi ら（2022）、数字は 16 × 0.05 × 0.95 ÷ 0.01² ＝ 7,600 で計算。線そのものは推測 → 出典: 07 §5-1（Kohavi・Deng・Vermeer, KDD 2022）
