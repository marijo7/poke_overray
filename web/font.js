// フォントの切り替え (overlay / party 共通)。
// 設定画面で選んだフォント (/api/state の display) を反映する。URL パラメータがあればそちらを優先:
//   font=メイリオ                 PC にインストール済みのフォント名
//   gfont=M PLUS Rounded 1c       Google Fonts のフォント (要インターネット)
//   fontfile=MyFont.ttf           web/fonts フォルダに置いたフォントファイル
//   fontweight=500                文字の太さ (100〜900。見出しは自動で太字)
(function () {
  const q = new URLSearchParams(location.search);
  const fromUrl = {};
  for (const k of ["font", "gfont", "fontfile", "fontweight"]) if (q.get(k)) fromUrl[k] = q.get(k);
  let lastKey = null;

  function apply(settings) {
    const s = Object.assign({}, settings || {}, fromUrl);  // URL 指定が優先
    const key = JSON.stringify(s);
    if (key === lastKey) return;
    lastKey = key;
    const root = document.documentElement;
    document.querySelectorAll("[data-overlay-font]").forEach((e) => e.remove());
    const families = [];
    if (s.gfont) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.dataset.overlayFont = "1";
      link.href = "https://fonts.googleapis.com/css2?family=" + encodeURIComponent(s.gfont).replace(/%20/g, "+")
        + ":wght@400;500;700;800&display=swap";
      document.head.appendChild(link);
      families.push(`"${String(s.gfont).replace(/"/g, "")}"`);
    }
    if (s.fontfile && /^[^/\\\\]+\.(ttf|otf|woff2?)$/i.test(s.fontfile)) {
      const style = document.createElement("style");
      style.dataset.overlayFont = "1";
      style.textContent = `@font-face { font-family: "OverlayCustomFont"; src: url("/fonts/${encodeURIComponent(s.fontfile)}"); }`;
      document.head.appendChild(style);
      families.push('"OverlayCustomFont"');
    }
    if (s.font) families.push(`"${String(s.font).replace(/"/g, "")}"`);
    if (families.length) root.style.setProperty("--font", families.join(", ") + ", var(--font-default)");
    else root.style.removeProperty("--font");
    const w = parseInt(s.fontweight, 10);
    if (w >= 100 && w <= 900) root.style.setProperty("--font-weight", w);
    else root.style.removeProperty("--font-weight");
  }

  window.applyOverlayFont = apply;
  apply({});  // サーバーの設定を受け取るまでは URL 指定だけで表示
})();
