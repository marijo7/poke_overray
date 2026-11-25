// フォントの切り替え (overlay / party 共通)。URL パラメータ:
//   font=メイリオ                 PC にインストール済みのフォント名
//   gfont=M PLUS Rounded 1c       Google Fonts のフォント (要インターネット)
//   fontfile=MyFont.ttf           web/fonts フォルダに置いたフォントファイル
//   fontweight=500                文字の太さ (100〜900。見出しは自動で太字)
(function () {
  const q = new URLSearchParams(location.search);
  const root = document.documentElement;
  const families = [];
  const gfont = q.get("gfont");
  if (gfont) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=" + encodeURIComponent(gfont).replace(/%20/g, "+")
      + ":wght@400;500;700;800&display=swap";
    document.head.appendChild(link);
    families.push(`"${gfont}"`);
  }
  const file = q.get("fontfile");
  if (file && /^[\w .()\-　-鿿]+\.(ttf|otf|woff2?)$/i.test(file)) {
    const style = document.createElement("style");
    style.textContent = `@font-face { font-family: "OverlayCustomFont"; src: url("/fonts/${encodeURIComponent(file)}"); }`;
    document.head.appendChild(style);
    families.push('"OverlayCustomFont"');
  }
  const font = q.get("font");
  if (font) families.push(`"${font.replace(/"/g, "")}"`);
  if (families.length) root.style.setProperty("--font", families.join(", ") + ", var(--font-default)");
  if (q.get("fontweight")) root.style.setProperty("--font-weight", parseInt(q.get("fontweight"), 10));
})();
