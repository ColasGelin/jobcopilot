// Runs inside one frame. Returns the visible form fields and signals about the page; never changes it.
() => {
  const SKIP = new Set(["hidden", "submit", "button", "image", "reset", "password"]);
  const KIND = { email: "email", tel: "tel", url: "url", file: "file", radio: "radio", checkbox: "checkbox" };
  const clean = (s) => (s || "").replace(/[✱*]/g, " ").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 || r.height > 0;
  };
  // File inputs are often hidden behind a styled button: count them when their container shows.
  const shown = (el) => visible(el) || (el.type === "file" && el.parentElement && visible(el.parentElement));
  const textOf = (el) => clean(el ? el.innerText || el.textContent : "");
  const nearbyText = (el) => {
    let node = el;
    for (let depth = 0; depth < 4 && node; depth++) {
      let prev = node.previousElementSibling;
      while (prev) {
        const t = textOf(prev);
        if (t) return t;
        prev = prev.previousElementSibling;
      }
      node = node.parentElement;
    }
    return "";
  };
  // A label that wraps its control would also contain the control's text (a select's options): drop it.
  const labelText = (label) => {
    const copy = label.cloneNode(true);
    copy.querySelectorAll("select, textarea, input, button").forEach((c) => c.remove());
    return clean(copy.innerText || copy.textContent);
  };
  const labelOf = (el) => {
    if (el.labels && el.labels.length) {
      const t = labelText(el.labels[0]);
      if (t) return t;
    }
    if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      const t = by.split(/\s+/).map((id) => textOf(document.getElementById(id))).join(" ").trim();
      if (t) return t;
    }
    return clean(el.getAttribute("placeholder")) || nearbyText(el);
  };
  const cssPath = (el) => {
    const parts = [];
    for (let node = el; node && node.nodeType === 1 && node !== document.body; node = node.parentElement) {
      const same = node.parentElement ? [...node.parentElement.children].filter((c) => c.tagName === node.tagName) : [];
      parts.unshift(node.tagName.toLowerCase() + (same.length > 1 ? `:nth-of-type(${same.indexOf(node) + 1})` : ""));
    }
    return "body > " + parts.join(" > ");
  };
  const selectorOf = (el) => {
    if (el.id) return "#" + CSS.escape(el.id);
    if (el.name && document.querySelectorAll(`[name="${CSS.escape(el.name)}"]`).length === 1)
      return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
    return cssPath(el);
  };
  const required = (el) => el.required || el.getAttribute("aria-required") === "true";

  const fields = [];
  const groups = new Map();
  let hasPassword = false;
  [...document.querySelectorAll("input, textarea, select")].forEach((el, index) => {
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (el.tagName === "INPUT" && type === "password" && visible(el)) hasPassword = true;
    if (el.tagName === "INPUT" && SKIP.has(type)) return;
    if (!shown(el) || el.disabled) return;
    const kind = el.tagName === "TEXTAREA" ? "textarea" : el.tagName === "SELECT" ? "select" : KIND[type] || "text";
    if ((kind === "radio" || kind === "checkbox") && el.name) {
      const optionLabel = labelOf(el);
      if (groups.has(el.name)) {
        groups.get(el.name).options.push(optionLabel);
        return;
      }
      const legend = el.closest("fieldset") && el.closest("fieldset").querySelector("legend");
      const field = {
        key: el.name, selector: `[name="${CSS.escape(el.name)}"]`, kind, options: [optionLabel],
        label: legend ? textOf(legend) : optionLabel, required: required(el), max_length: null, description: "",
        _single: !legend,
      };
      groups.set(el.name, field);
      fields.push(field);
      return;
    }
    fields.push({
      key: el.id || el.name || `field_${index}`, selector: selectorOf(el), label: labelOf(el), kind,
      options: kind === "select" ? [...el.options].filter((o) => o.value !== "" && clean(o.text)).map((o) => clean(o.text)) : [],
      required: required(el), max_length: el.maxLength > 0 ? el.maxLength : null, description: "",
    });
  });
  // A checkbox group without a legend: label it from the text before the first box.
  for (const f of fields) {
    if (f._single && f.options.length > 1) {
      const first = document.querySelector(f.selector);
      f.label = nearbyText(first.closest("label") || first) || f.label;
    }
    delete f._single;
  }
  const captchaFrames = [...document.querySelectorAll("iframe")].some((f) =>
    /recaptcha|hcaptcha|turnstile|challenges\.cloudflare/i.test((f.getAttribute("src") || "") + " " + (f.title || "")));
  return {
    fields,
    text: (document.body ? document.body.innerText : "").slice(0, 20000),
    has_password: hasPassword,
    captcha: captchaFrames || !!document.querySelector(".g-recaptcha, .h-captcha, .cf-turnstile"),
    form_count: document.forms.length,
  };
}
