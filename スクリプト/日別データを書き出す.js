/**
 * 日別データを書き出す（Google 広告スクリプト。読むだけ）
 *
 * 何をするか:
 *   このアカウントのキャンペーン別・日別の実績を、レポートで読むだけ（AdsApp.search と GAQL）。
 *   読んだ行を、CONFIG.SHEET_URL のスプレッドシートの「日別データ」タブの A〜I 列に書く。
 *
 * 書く列（ads-mcp の report_sheet_plan が作る「日別データ」と同じ）:
 *   A 日付 / B 媒体（「Google 広告」） / C キャンペーン ID（文字として） / D キャンペーン /
 *   E 表示回数 / F クリック数 / G 費用（円。micros ÷ 1,000,000） / H コンバージョン / I コンバージョン値（円）
 *
 * 守ること:
 *   - 広告アカウントは読むだけ。キャンペーン・広告・キーワード・入札・予算を変える呼び出しは、このファイルに 1 つも無い。
 *   - 書くのは「日別データ」タブの A〜I 列だけ。J 列から右（率と単価の ARRAYFORMULA）と、ほかのタブには触らない。
 *   - 読み直す日（昨日から LOOKBACK_DAYS 日さかのぼる）の行は、いったん消して書き直す。それより前の行はそのまま残す。
 *     コンバージョンは後から足されるので、直近の日は毎日読み直して、新しい数字に置きかえる。
 *   - DRY_RUN が true（既定）のときは、書く内容をログに出すだけで、シートには何も書かない。
 *
 * 入れ方は同じフォルダの「入れ方.md」。
 */

var CONFIG = {
  // 書き先のスプレッドシートの URL。ここに運用シートの URL を貼る（下は見本の形で、本物の URL ではない）
  SHEET_URL: 'https://docs.google.com/spreadsheets/d/ここにシートのIDを入れる/edit',
  // 書き先のタブの名前（変えない）
  TAB_NAME: '日別データ',
  // 昨日から何日さかのぼって読み直すか（1〜30）。成果の遅れを取り込むため、7〜30 日にする
  LOOKBACK_DAYS: 14,
  // true のあいだは書かない（ログに出すだけ）。ログを確かめてから false にする
  DRY_RUN: true,
  // 媒体の列に書く名前
  MEDIA: 'Google 広告',
  // ログに出す行の数（DRY_RUN のとき）
  LOG_ROWS: 10
};

// 「日別データ」の見出し（A〜I）。シートの 1 行目と違えば、書かずに止める
var HEADER = ['日付', '媒体', 'キャンペーン ID', 'キャンペーン', '表示回数', 'クリック数', '費用（円）', 'コンバージョン',
              'コンバージョン値（円）'];
var WIDTH = HEADER.length;  // 9 列（A〜I）

function main() {
  checkConfig_();
  var tz = AdsApp.currentAccount().getTimeZone();
  var range = dateRange_(tz, CONFIG.LOOKBACK_DAYS);
  Logger.log('読む期間: ' + range.start + '〜' + range.end + '（' + tz + ' の日付、' + CONFIG.LOOKBACK_DAYS + ' 日）');

  var fresh = readRows_(range);
  Logger.log('読んだ行: ' + fresh.length + ' 行');

  var ss = SpreadsheetApp.openByUrl(CONFIG.SHEET_URL);
  var sheet = ss.getSheetByName(CONFIG.TAB_NAME);
  if (!sheet) {
    throw new Error('「' + CONFIG.TAB_NAME + '」タブが無い。先に ads-mcp の report_sheet_plan でシートを作る');
  }
  checkHeader_(sheet);

  var sheetTz = ss.getSpreadsheetTimeZone();
  var old = readSheet_(sheet, sheetTz);
  var kept = [];
  var dropped = 0;
  for (var i = 0; i < old.length; i++) {
    var d = old[i][0];
    if (d >= range.start && d <= range.end) {
      dropped++;  // 読み直す日の行は書き直す
    } else {
      kept.push(old[i]);
    }
  }
  var all = kept.concat(fresh);
  all.sort(function (a, b) {
    if (a[0] !== b[0]) return a[0] < b[0] ? -1 : 1;
    return String(a[3]) < String(b[3]) ? -1 : (String(a[3]) > String(b[3]) ? 1 : 0);
  });

  Logger.log('シートの今の行: ' + old.length + ' 行（うち読み直す日の行 ' + dropped + ' 行を置きかえる）');
  Logger.log('書いたあとの行: ' + all.length + ' 行（残す ' + kept.length + ' ＋ 新しく読んだ ' + fresh.length + '）');
  logTotals_(fresh);

  if (CONFIG.DRY_RUN) {
    Logger.log('DRY_RUN = true なので、シートには書いていない。書く行の先頭:');
    for (var j = 0; j < Math.min(CONFIG.LOG_ROWS, fresh.length); j++) {
      Logger.log('  ' + fresh[j].join(' | '));
    }
    return;
  }
  writeSheet_(sheet, all);
  Logger.log('書いた: 「' + CONFIG.TAB_NAME + '」の A2:I' + (all.length + 1));
}

// ---------------- 読む（レポートだけ） ----------------

function readRows_(range) {
  var query =
    'SELECT campaign.id, campaign.name, segments.date, metrics.impressions, metrics.clicks, ' +
    'metrics.cost_micros, metrics.conversions, metrics.conversions_value ' +
    'FROM campaign ' +
    "WHERE segments.date BETWEEN '" + range.start + "' AND '" + range.end + "'";
  var it = AdsApp.search(query);
  var out = [];
  while (it.hasNext()) {
    var r = it.next();
    var m = r.metrics;
    var imp = Number(m.impressions || 0);
    var clk = Number(m.clicks || 0);
    var cost = Number(m.costMicros || 0) / 1000000;
    var conv = Number(m.conversions || 0);
    var val = Number(m.conversionsValue || 0);
    if (!imp && !clk && !cost && !conv && !val) continue;  // 数字が全部 0 の行は書かない（合計は変わらない）
    out.push([
      r.segments.date,                 // A 日付（YYYY-MM-DD）
      CONFIG.MEDIA,                    // B 媒体
      "'" + String(r.campaign.id),     // C キャンペーン ID（先頭の ' で文字として入れる）
      r.campaign.name,                 // D キャンペーン
      imp,                             // E 表示回数
      clk,                             // F クリック数
      round6_(cost),                   // G 費用（円。micros の精度のまま）
      round6_(conv),                   // H コンバージョン
      round6_(val)                     // I コンバージョン値（円）
    ]);
  }
  return out;
}

// ---------------- シート（日別データの A〜I だけ） ----------------

function checkHeader_(sheet) {
  var head = sheet.getRange(1, 1, 1, WIDTH).getDisplayValues()[0];
  for (var i = 0; i < WIDTH; i++) {
    if (String(head[i]).trim() !== HEADER[i]) {
      throw new Error('「' + CONFIG.TAB_NAME + '」の 1 行目が想定と違う（' + (i + 1) + ' 列目: 「' + head[i] + '」、想定: 「' +
                      HEADER[i] + '」）。書かずに止めた');
    }
  }
}

function readSheet_(sheet, sheetTz) {
  var last = sheet.getLastRow();
  if (last < 2) return [];
  var values = sheet.getRange(2, 1, last - 1, WIDTH).getValues();
  var out = [];
  for (var i = 0; i < values.length; i++) {
    var v = values[i];
    if (v[0] === '' || v[0] === null) continue;  // A 列が空の行は捨てる（J 列の式だけの行など）
    var row = v.slice(0, WIDTH);
    row[0] = toDateText_(v[0], sheetTz);
    row[2] = "'" + String(v[2]).replace(/^'/, '');
    out.push(row);
  }
  return out;
}

function writeSheet_(sheet, rows) {
  var last = sheet.getLastRow();
  if (last >= 2) {
    sheet.getRange(2, 1, last - 1, WIDTH).clearContent();  // A〜I の中身だけ消す（J 列から右と書式はそのまま）
  }
  if (rows.length > 0) {
    sheet.getRange(2, 1, rows.length, WIDTH).setValues(rows);
  }
}

// ---------------- 小さな部品 ----------------

function checkConfig_() {
  if (!CONFIG.SHEET_URL || CONFIG.SHEET_URL.indexOf('ここに') >= 0) {
    throw new Error('CONFIG.SHEET_URL に、運用シートの URL を入れる');
  }
  var n = Number(CONFIG.LOOKBACK_DAYS);
  if (!(n >= 1 && n <= 30) || Math.floor(n) !== n) {
    throw new Error('CONFIG.LOOKBACK_DAYS は 1〜30 の整数にする');
  }
}

function dateRange_(tz, days) {
  var now = new Date();
  var end = new Date(now.getTime() - 24 * 3600 * 1000);                 // 昨日
  var start = new Date(now.getTime() - days * 24 * 3600 * 1000);        // 昨日から days 日分
  return {
    start: Utilities.formatDate(start, tz, 'yyyy-MM-dd'),
    end: Utilities.formatDate(end, tz, 'yyyy-MM-dd')
  };
}

function toDateText_(v, tz) {
  if (Object.prototype.toString.call(v) === '[object Date]') {
    return Utilities.formatDate(v, tz, 'yyyy-MM-dd');
  }
  var s = String(v).trim().replace(/\//g, '-');
  var m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
  if (!m) return s;
  return m[1] + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[3]).slice(-2);
}

function round6_(x) {
  return Math.round(x * 1000000) / 1000000;
}

function logTotals_(rows) {
  var t = [0, 0, 0, 0, 0];
  for (var i = 0; i < rows.length; i++) {
    for (var k = 0; k < 5; k++) t[k] += Number(rows[i][4 + k]);
  }
  Logger.log('読んだ期間の合計: 表示 ' + t[0] + ' 回、クリック ' + t[1] + ' 回、費用 ' + Math.round(t[2]) + ' 円、' +
             'コンバージョン ' + round6_(t[3]) + ' 件、コンバージョン値 ' + Math.round(t[4]) + ' 円');
}
