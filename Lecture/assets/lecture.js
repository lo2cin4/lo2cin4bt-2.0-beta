const pageGroups = [
  {
    label: "新手主線",
    pages: [
      ["首頁", "index.html"],
      ["00 開始使用與 AI Manual", "Module_00_Getting_Started/index.html"],
      ["01 系統地圖", "Module_01_System_Map/index.html"],
      ["02 資料來源與安裝要求", "Module_02_Data_Providers/index.html"],
      ["03 StrategyRun Config", "Module_03_Strategy_Run_Config/index.html"],
      ["04 回測入門", "Module_04_Backtest_Basics/index.html"],
      ["05 策略類型與系統對應", "Module_05_Strategy_Semantics/index.html"],
      ["06 Parameter Matrix 參數矩陣", "Module_06_Parameter_Matrix/index.html"],
      ["07 Backtests 回測報告閱讀", "Module_07_Backtests_Report/index.html"],
      ["08 WFA 滾動驗證", "Module_08_WFA_Rolling_Validation/index.html"],
    ],
  },
  {
    label: "進階與維護者",
    pages: [
      ["09 Accounting / Risk / Invariant", "Module_09_Accounting_Risk_Invariants/index.html"],
      ["10 資料來源擴充", "Module_10_Data_Provider_Extension/index.html"],
      ["11 Factor Analysis 因子分析尚未開發功能", "Module_11_Factor_Analysis_Preview/index.html"],
      ["12 Validation Tools 驗收工具", "Module_12_Validation_Tools/index.html"],
      ["13 C4 Architecture 架構圖", "Module_13_C4_Architecture/index.html"],
    ],
  },
  {
    label: "實作練習",
    pages: [
      ["Lab 01 跑一個回測", "Lab_01_Run_A_Backtest/index.html"],
      ["Lab 02 Parameter Matrix 參數矩陣", "Lab_02_Parameter_Matrix/index.html"],
      ["Lab 03 WFA 滾動驗證", "Lab_03_WFA_Rolling/index.html"],
      ["Lab 04 資料來源檢查", "Lab_04_Provider_Check/index.html"],
    ],
  },
  {
    label: "附錄",
    pages: [["Appendix", "Appendix/index.html"]],
  },
];

function basePrefix() {
  const p = location.pathname.replaceAll("\\", "/");
  return p.includes("/Module_") || p.includes("/Lab_") || p.includes("/Appendix/") ? "../" : "";
}

function buildSidebar() {
  const prefix = basePrefix();
  const current = location.pathname.replaceAll("\\", "/");
  const nav = document.querySelector("[data-nav]");
  if (!nav) return;

  nav.innerHTML = pageGroups
    .map((group) => {
      const links = group.pages
        .map(([title, href]) => {
          const active = current.endsWith(href) || (href === "index.html" && /\/Lecture\/index.html$/.test(current));
          return `<a class="${active ? "active" : ""}" href="${prefix + href}" data-title="${`${group.label} ${title}`.toLowerCase()}">${title}</a>`;
        })
        .join("");
      return `<section class="nav-group"><div class="nav-group-label">${group.label}</div>${links}</section>`;
    })
    .join("");
}

function enableSearch() {
  const input = document.querySelector("[data-search]");
  if (!input) return;

  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    document.querySelectorAll("[data-nav] a").forEach((a) => {
      a.classList.toggle("hidden", Boolean(q) && !a.dataset.title.includes(q));
    });
  });
}

function buildToc() {
  const article = document.querySelector("article");
  if (!article || article.querySelector(".toc")) return;
  const headings = [...article.querySelectorAll("h2")];
  if (headings.length < 3) return;

  const toc = document.createElement("nav");
  toc.className = "toc";
  toc.innerHTML = `<strong>本頁目錄</strong><br>${headings
    .map((h, i) => {
      if (!h.id) h.id = `section-${i + 1}`;
      return `<a href="#${h.id}">${h.textContent}</a>`;
    })
    .join("")}`;
  const firstParagraph = article.querySelector("p:not(.kicker)");
  if (firstParagraph) firstParagraph.insertAdjacentElement("afterend", toc);
}

function addBackToTop() {
  const button = document.createElement("button");
  button.className = "back-to-top";
  button.type = "button";
  button.textContent = "↑";
  button.title = "回到頁首";
  button.addEventListener("click", () => scrollTo({ top: 0, behavior: "smooth" }));
  document.body.appendChild(button);
}

function loadMermaid() {
  if (!document.querySelector(".mermaid")) return Promise.resolve(false);
  if (window.mermaid) return Promise.resolve(true);

  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js";
    script.onload = () => resolve(true);
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

async function renderMermaid() {
  try {
    const hasMermaid = await loadMermaid();
    if (!hasMermaid || !window.mermaid) return;

    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      themeVariables: {
        background: "#0f1419",
        primaryColor: "#1b2433",
        primaryTextColor: "#eef3f8",
        primaryBorderColor: "#4fc3f7",
        lineColor: "#4fc3f7",
        secondaryColor: "#2a3142",
        tertiaryColor: "#111827",
        edgeLabelBackground: "#101722",
        clusterBkg: "#111827",
        clusterBorder: "#ffd54f",
        fontFamily: '"Noto Sans TC", "Microsoft JhengHei", "Segoe UI", Arial, sans-serif',
      },
      flowchart: {
        htmlLabels: true,
        curve: "basis",
      },
    });

    await window.mermaid.run({ querySelector: ".mermaid" });
  } catch (error) {
    document.querySelectorAll(".mermaid").forEach((block) => {
      block.dataset.fallback = "Mermaid 圖表未能載入。這通常是因為 file:// 模式無法連接 CDN；下方會保留原始圖表語法，使用本地伺服器或有網絡時會自動渲染。";
      block.classList.add("mermaid-failed");
    });
    console.error("Mermaid failed to render", error);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  buildSidebar();
  enableSearch();
  buildToc();
  renderMermaid();
  addBackToTop();
});
