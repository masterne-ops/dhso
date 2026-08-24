/** 思考/目标用的轻量 Markdown。先转义再排版，禁止 HTML / javascript: 链接。 */
(function (global) {
  function mdEsc(s) {
    return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function mdSafeHref(u) {
    const s = String(u || "").trim();
    if (!/^(https?:\/\/|mailto:)/i.test(s)) return "";
    return mdEsc(s).replace(/"/g, "&quot;");
  }
  function mdInline(s) {
    s = s.replace(/`([^`]+)`/g, function (_, c) {
      return "<code>" + c + "</code>";
    });
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+|mailto:[^)\s]+)\)/g, function (_, text, url) {
      const href = mdSafeHref(url.replace(/&amp;/g, "&"));
      if (!href) return text;
      return '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + text + "</a>";
    });
    s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\*(.+?)\*/g, "<em>$1</em>");
    return s;
  }
  function mdHtml(src) {
    let t = String(src || "").replace(/\r\n/g, "\n");
    if (!t.trim()) return "";
    const fences = [];
    t = t.replace(/```[^\n]*\n([\s\S]*?)```/g, function (_, code) {
      const i = fences.length;
      fences.push(
        '<pre class="mdPre"><code>' + mdEsc(code.replace(/\n$/, "")) + "</code></pre>"
      );
      return "\n\uE000" + i + "\uE001\n";
    });
    t = mdEsc(t);
    const lines = t.split("\n");
    const out = [];
    let i = 0;
    const para = [];
    function flushPara() {
      if (!para.length) return;
      out.push("<p>" + para.map(mdInline).join("<br>") + "</p>");
      para.length = 0;
    }
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(/^\uE000(\d+)\uE001$/);
      if (fence) {
        flushPara();
        out.push(fences[+fence[1]] || "");
        i++;
        continue;
      }
      const h = line.match(/^(#{1,3}) (.+)$/);
      if (h) {
        flushPara();
        const n = h[1].length;
        out.push("<h" + n + ">" + mdInline(h[2]) + "</h" + n + ">");
        i++;
        continue;
      }
      if (/^(&gt; ?)/.test(line)) {
        flushPara();
        const qs = [];
        while (i < lines.length && /^(&gt; ?)/.test(lines[i])) {
          qs.push(lines[i].replace(/^(&gt; ?)/, ""));
          i++;
        }
        out.push("<blockquote>" + qs.map(mdInline).join("<br>") + "</blockquote>");
        continue;
      }
      if (/^([-*] |\d+\. )/.test(line)) {
        flushPara();
        const ul = /^[-*] /.test(line);
        const re = ul ? /^[-*] / : /^\d+\. /;
        const items = [];
        while (i < lines.length && re.test(lines[i])) {
          items.push("<li>" + mdInline(lines[i].replace(re, "")) + "</li>");
          i++;
        }
        out.push((ul ? "<ul>" : "<ol>") + items.join("") + (ul ? "</ul>" : "</ol>"));
        continue;
      }
      if (/^(-{3,}|\*{3,})$/.test(line)) {
        flushPara();
        out.push("<hr>");
        i++;
        continue;
      }
      if (line === "") {
        flushPara();
        i++;
        continue;
      }
      para.push(line);
      i++;
    }
    flushPara();
    return out.join("");
  }
  global.mdHtml = mdHtml;
})(typeof window !== "undefined" ? window : globalThis);
