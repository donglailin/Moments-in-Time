/* 时迹 · 文件时间筛选器 —— 前端交互（文件夹目录视图） */

(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const CHUNK = 250;          // 每次渲染多少行
  const POLL_MS = 140;

  const S = {
    boot: null,
    settings: {},
    items: [],
    byId: new Map(),
    filtered: [],
    tree: null,           // 文件夹树根节点
    node: null,           // 当前所在文件夹节点
    path: [],             // 当前文件夹的路径分段（相对源文件夹）
    view: [],             // 当前层级的条目（文件夹 + 文件）
    selected: new Set(),
    selectedBytes: 0,     // 已选文件总大小（增量维护，避免每次全量遍历）
    exportedIds: new Set(),
    rendered: 0,
    query: "",
    typeFilter: "all",
    sort: "mtime_desc",
    scanning: false,
    exporting: false,
    scanMeta: null,
    lastExport: null,
    pollTimer: null,
    pendingDrop: false,
    browserMode: false,
  };

  const api = () => window.pywebview.api;

  /* ---------------- 基础工具 ---------------- */
  const htmlEscape = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  function fmtSize(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${n} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = n / 1024;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) {
      value /= 1024;
      i += 1;
    }
    return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[i]}`;
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function fmtTime(ms) {
    const d = new Date(Number(ms) || 0);
    if (Number.isNaN(d.getTime())) return "-";
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fmtDate(d) {
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function parseDate(value) {
    if (!value) return null;
    const parts = value.split("-").map(Number);
    if (parts.length !== 3 || parts.some(Number.isNaN)) return null;
    const d = new Date(parts[0], parts[1] - 1, parts[2]);
    d.setHours(0, 0, 0, 0);
    return d;
  }

  const startOfDay = (d = new Date()) => {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    return x;
  };

  function debounce(fn, wait) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
  }

  const FAMILIES = {
    image: new Set(["jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "ico", "tif", "tiff", "heic", "raw", "psd", "ai"]),
    doc: new Set(["doc", "docx", "xls", "xlsx", "ppt", "pptx", "pdf", "txt", "md", "rtf", "csv", "odt", "ods", "epub", "pages", "numbers", "key"]),
    video: new Set(["mp4", "mov", "avi", "mkv", "flv", "wmv", "webm", "m4v", "mpg", "mpeg", "ts", "3gp"]),
    audio: new Set(["mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "ape", "aiff"]),
    archive: new Set(["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "cab"]),
    code: new Set(["js", "ts", "jsx", "tsx", "py", "java", "c", "cpp", "h", "cs", "go", "rs", "php", "html", "htm", "css", "scss", "json", "xml", "yml", "yaml", "sql", "sh", "bat", "ps1", "vue", "rb", "kt", "swift"]),
    app: new Set(["exe", "msi", "dll", "apk", "dmg", "app", "jar", "bin"]),
  };

  const FAMILY_LABELS = {
    image: "图片",
    doc: "文档",
    video: "视频",
    audio: "音频",
    archive: "压缩包",
    code: "代码",
    app: "程序",
    other: "其他",
  };

  const FAMILY_ICONS = {
    image: "i-image",
    doc: "i-doc",
    video: "i-video",
    audio: "i-audio",
    archive: "i-archive",
    code: "i-code",
    app: "i-app",
    other: "i-file",
  };

  function familyOf(ext) {
    const key = String(ext || "").toLowerCase();
    for (const [family, set] of Object.entries(FAMILIES)) {
      if (set.has(key)) return family;
    }
    return "other";
  }

  /* ---------------- 时间范围 ---------------- */
  function rangeForPreset(preset) {
    const now = new Date();
    const day0 = startOfDay(now);
    const nextDay = new Date(day0);
    nextDay.setDate(nextDay.getDate() + 1);

    if (preset === "all") {
      return { start: null, end: null, label: "不限时间，扫描全部文件" };
    }
    if (preset === "today") {
      return {
        start: day0,
        end: nextDay,
        label: `${fmtDate(day0)} 00:00 起（今天）`,
      };
    }
    if (preset === "week") {
      const start = new Date(day0);
      start.setDate(start.getDate() - 6);
      return { start, end: nextDay, label: `${fmtDate(start)} 00:00 — ${fmtDate(day0)} 24:00（近 7 天）` };
    }
    if (preset === "month30") {
      const start = new Date(day0);
      start.setDate(start.getDate() - 29);
      return { start, end: nextDay, label: `${fmtDate(start)} 00:00 — ${fmtDate(day0)} 24:00（近 30 天）` };
    }
    if (preset === "month") {
      const start = new Date(now.getFullYear(), now.getMonth(), 1);
      return { start, end: nextDay, label: `${fmtDate(start)} 00:00 — 今天 24:00（本月）` };
    }
    // 自定义
    const start = parseDate(S.settings.customStart);
    const end = parseDate(S.settings.customEnd);
    if (!start || !end) {
      return { start: null, end: null, label: "请选择开始与结束日期", invalid: true };
    }
    if (end < start) {
      return { start, end: null, label: "结束日期早于开始日期", invalid: true };
    }
    const endNext = new Date(end);
    endNext.setDate(endNext.getDate() + 1);
    return {
      start,
      end: endNext,
      label: `${fmtDate(start)} 00:00 — ${fmtDate(end)} 24:00`,
    };
  }

  /* ---------------- 界面状态 ---------------- */
  function currentRange() {
    return rangeForPreset(S.settings.preset);
  }

  function updatePresetUI() {
    $$("#preset-grid .preset").forEach((btn) => {
      const active = btn.dataset.preset === S.settings.preset;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    $("#custom-range").hidden = S.settings.preset !== "custom";
    const range = currentRange();
    $("#range-note").textContent = range.label;
    $("#range-note").style.color = range.invalid ? "var(--danger)" : "";
  }

  function setSource(path) {
    S.settings.source = path || "";
    const dropzone = $("#drop-zone");
    dropzone.classList.toggle("has-path", Boolean(path));
    $("#source-path").textContent = path || "尚未选择";
    $("#source-path").title = path || "";
    $("#dz-title").textContent = path ? "源文件夹" : "拖拽文件夹到此处";
    $("#btn-scan").disabled = !path || S.scanning;
    if (!path) return;
    if (S.items.length) resetResults();
    persist();
  }

  function setDest(path) {
    S.settings.dest = path || "";
    const field = $("#dest-path");
    field.classList.toggle("has-path", Boolean(path));
    field.title = path || "";
    $(".dest-text", field).textContent = path || "未选择导出文件夹";
    updateActionState();
    persist();
  }

  function currentIds() {
    return S.node ? S.node.ids : new Set();
  }

  function updateActionState() {
    const hasDest = Boolean(S.settings.dest);
    const hasSelection = S.selected.size > 0;
    const busy = S.scanning || S.exporting;
    $("#btn-export").disabled = !hasDest || !hasSelection || busy;
    $("#btn-select-all").disabled = !currentIds().size || busy;
    $("#btn-clear-selection").disabled = !hasSelection || busy;
    $("#btn-scan").disabled = S.scanning || !S.settings.source;
    $$("#mode-seg .seg").forEach((btn) => {
      const on = btn.dataset.mode === S.settings.mode;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
    $("#opt-preserve").checked = Boolean(S.settings.preserve);
    $("#opt-conflict").value = S.settings.conflict || "rename";
  }

  function updateSelectionSummary() {
    $("#selection-summary").innerHTML = `已选 <b>${S.selected.size}</b> 个文件 · ${htmlEscape(fmtSize(S.selectedBytes))}`;
    updateActionState();
  }

  function updateResultHeader() {
    const root = S.tree;
    $("#sum-folders").textContent = root ? root.folderCount.toLocaleString() : "0";
    $("#sum-files").textContent = S.filtered.length.toLocaleString();
    $("#sum-size").textContent = fmtSize(root ? root.size : 0);
    $("#filter-flag").hidden = !(S.query || S.typeFilter !== "all");

    if (!S.scanMeta) {
      $("#scan-note").textContent = "";
      return;
    }
    const parts = [];
    if (S.scanMeta.duration_ms) parts.push(`用时 ${(S.scanMeta.duration_ms / 1000).toFixed(1)} 秒`);
    if (S.scanMeta.error_count) parts.push(`${S.scanMeta.error_count} 个位置无法访问`);
    if (S.scanMeta.truncated) parts.push("已达上限，仅展示前 100000 个");
    $("#scan-note").textContent = parts.join(" · ");
  }

  function updateCrumbs() {
    const labels = ["全部结果", ...S.path];
    let html = "";
    labels.forEach((name, index) => {
      const isLast = index === labels.length - 1;
      if (index > 0) html += '<svg class="crumb-sep"><use href="#i-chevron"/></svg>';
      html += isLast
        ? `<span class="crumb is-current" title="${htmlEscape(name)}">${htmlEscape(name)}</span>`
        : `<button class="crumb" data-crumb="${index}" title="返回 ${htmlEscape(name)}">${htmlEscape(name)}</button>`;
    });
    $("#crumbs").innerHTML = html;
    $("#btn-up").disabled = S.path.length === 0;

    const folders = S.view.filter((e) => e.kind === "folder").length;
    const files = S.view.length - folders;
    $("#level-meta").textContent = folders
      ? `本层 ${folders} 个文件夹 · ${files} 个文件`
      : `本层 ${files} 个文件`;
  }

  function updateTableMode() {
    const hasFolder = S.view.some((e) => e.kind === "folder");
    $("#file-table").classList.toggle("mode-folders", hasFolder);
    $("#file-table").classList.toggle("mode-files", !hasFolder);
  }

  /* ---------------- 文件夹树 ---------------- */
  function makeNode(name, path) {
    return {
      name,
      path,
      parent: null,
      children: new Map(),
      files: [],
      ids: new Set(),
      picked: 0,       // 该节点子树中已被勾选的文件数（增量维护）
      size: 0,
      latest: 0,
      fileCount: 0,
      folderCount: 0,
    };
  }

  function finalizeNode(node) {
    const ids = new Set();
    let size = 0;
    let latest = 0;
    let fileCount = 0;
    let folderCount = 0;

    node.files.forEach((it) => {
      ids.add(it.id);
      size += Number(it.size) || 0;
      latest = Math.max(latest, Number(it.mtime) || 0);
      fileCount += 1;
    });

    node.children.forEach((child) => {
      finalizeNode(child);
      child.ids.forEach((id) => ids.add(id));
      size += child.size;
      latest = Math.max(latest, child.latest);
      fileCount += child.fileCount;
      folderCount += child.folderCount + (child.fileCount > 0 ? 1 : 0);
    });

    node.ids = ids;
    node.size = size;
    node.latest = latest;
    node.fileCount = fileCount;
    node.folderCount = folderCount;
    return node;
  }

  function buildTree(items) {
    const root = makeNode("", "");
    items.forEach((it) => {
      const parts = String(it.rel || it.name).split("\\");
      const fileName = parts.pop();
      let node = root;
      let relPath = "";
      parts.forEach((part) => {
        relPath = relPath ? `${relPath}\\${part}` : part;
        let child = node.children.get(part);
        if (!child) {
          child = makeNode(part, relPath);
          child.parent = node;
          node.children.set(part, child);
        }
        node = child;
      });
      node.files.push(it);
      it.__node = node;   // 反查所属节点，供勾选计数沿父链更新
    });
    return finalizeNode(root);
  }

  function nodeAt(pathArr) {
    let node = S.tree;
    for (const name of pathArr) {
      if (!node) return null;
      node = node.children.get(name) || null;
    }
    return node;
  }

  function nodeByPath(path) {
    if (!S.tree) return null;
    if (!path) return S.tree;
    return nodeAt(path.split("\\"));
  }

  function cmpEntries(a, b) {
    const an = a.kind === "folder" ? a.node.name : a.item.name;
    const bn = b.kind === "folder" ? b.node.name : b.item.name;
    const am = a.kind === "folder" ? a.node.latest : Number(a.item.mtime) || 0;
    const bm = b.kind === "folder" ? b.node.latest : Number(b.item.mtime) || 0;
    const as = a.kind === "folder" ? a.node.size : Number(a.item.size) || 0;
    const bs = b.kind === "folder" ? b.node.size : Number(b.item.size) || 0;
    const byName = () => an.localeCompare(bn, "zh-CN");
    switch (S.sort) {
      case "mtime_asc":
        return am - bm || byName();
      case "name_asc":
        return byName();
      case "name_desc":
        return bn.localeCompare(an, "zh-CN");
      case "size_desc":
        return bs - as || byName();
      case "size_asc":
        return as - bs || byName();
      default:
        return bm - am || byName();
    }
  }

  function currentEntries() {
    const node = S.node || S.tree;
    if (!node) return [];
    const folders = [];
    node.children.forEach((child) => {
      if (child.fileCount > 0) folders.push({ kind: "folder", node: child });
    });
    const files = node.files.map((item) => ({ kind: "file", item }));
    folders.sort(cmpEntries);
    files.sort(cmpEntries);
    return folders.concat(files);
  }

  /* ---------------- 结果视图 ---------------- */
  function resetResults() {
    S.items = [];
    S.byId = new Map();
    S.filtered = [];
    S.tree = makeNode("", "");
    S.node = null;
    S.path = [];
    S.view = [];
    S.selected.clear();
    S.selectedBytes = 0;
    S.exportedIds.clear();
    S.scanMeta = null;
    S.rendered = 0;
    $("#tbody").innerHTML = "";
    $("#type-filter").innerHTML = '<option value="all">全部类型</option>';
    S.typeFilter = "all";
    updateTableMode();
    updateCrumbs();
    updateEmptyState();
    updateResultHeader();
    updateSelectionSummary();
  }

  function updateTypeFilterOptions() {
    const counts = {};
    S.items.forEach((it) => {
      const family = familyOf(it.ext);
      counts[family] = (counts[family] || 0) + 1;
    });
    const options = ['<option value="all">全部类型</option>'];
    Object.keys(FAMILY_LABELS).forEach((family) => {
      if (!counts[family]) return;
      options.push(`<option value="${family}">${FAMILY_LABELS[family]} (${counts[family].toLocaleString()})</option>`);
    });
    const select = $("#type-filter");
    select.innerHTML = options.join("");
    if (!counts[S.typeFilter]) S.typeFilter = "all";
    select.value = S.typeFilter;
  }

  function rebuildView() {
    S.query = $("#search-input").value.trim().toLowerCase();
    S.typeFilter = $("#type-filter").value;
    S.sort = $("#sort-select").value;

    let list = S.items;
    if (S.typeFilter !== "all") {
      list = list.filter((it) => familyOf(it.ext) === S.typeFilter);
    }
    if (S.query) {
      list = list.filter(
        (it) => it.name.toLowerCase().includes(S.query) || it.rel.toLowerCase().includes(S.query),
      );
    }
    S.filtered = list;

    S.tree = buildTree(list);
    // 当前路径若因筛选失效，则回退到最近的可用上级
    let path = S.path.slice();
    while (path.length && !nodeAt(path)) path.pop();
    S.path = path;
    S.node = nodeAt(path) || S.tree;
    S.view = currentEntries();
    // 树已重建，把既有选择状态折算回新节点的 picked 计数
    recountSelection();

    S.rendered = 0;
    $("#tbody").innerHTML = "";
    renderRows();
    updateTableMode();
    updateCrumbs();
    updateResultHeader();
    updateEmptyState();
    updateCheckAll();
    updateActionState();
  }

  function folderState(node) {
    if (!node.ids.size || node.picked === 0) return "none";
    return node.picked >= node.ids.size ? "all" : "partial";
  }

  /* ---------------- 勾选计数（增量维护，避免 O(文件夹×文件) 全量扫描） ---------------- */
  function walkNodes(node, fn) {
    fn(node);
    node.children.forEach((child) => walkNodes(child, fn));
  }

  // 选中状态变化后重算 picked / selectedBytes（仅在重建树或清空选择时调用）
  function recountSelection() {
    if (!S.tree) return;
    walkNodes(S.tree, (node) => {
      node.picked = 0;
    });
    S.selectedBytes = 0;
    S.selected.forEach((id) => {
      const item = S.byId.get(id);
      if (!item) return;
      S.selectedBytes += Number(item.size) || 0;
      let cur = item.__node || null;
      while (cur) {
        cur.picked += 1;
        cur = cur.parent;
      }
    });
  }

  function folderRowHtml(node) {
    const state = folderState(node);
    const sub = node.folderCount ? `${node.folderCount} 个子文件夹` : "无下级文件夹";
    return `<tr class="folder-row${state === "all" ? " is-selected" : ""}" data-kind="folder" data-folder="${htmlEscape(node.path)}">
      <td class="col-check"><label class="check"><input type="checkbox" data-fcheck="${htmlEscape(node.path)}" ${state === "all" ? "checked" : ""} /><span></span></label></td>
      <td class="col-type"><span class="type-ico t-folder"><svg><use href="#i-folder"/></svg></span></td>
      <td class="col-name">
        <div class="row-name">
          <button class="folder-open" data-open="${htmlEscape(node.path)}" title="打开 ${htmlEscape(node.name)}">
            <span class="fname">${htmlEscape(node.name)}</span>
          </button>
          <span class="f-sub">${htmlEscape(sub)}</span>
        </div>
      </td>
      <td class="col-count"><span class="hit-count">${node.fileCount.toLocaleString()}</span></td>
      <td class="col-size"><span class="size">${htmlEscape(fmtSize(node.size))}</span></td>
      <td class="col-time"><span class="mtime">${htmlEscape(fmtTime(node.latest))}</span></td>
      <td class="col-act"><button class="icon-btn" data-open="${htmlEscape(node.path)}" title="打开此文件夹"><svg><use href="#i-chevron"/></svg></button></td>
    </tr>`;
  }

  function fileRowHtml(item) {
    const family = familyOf(item.ext);
    const checked = S.selected.has(item.id);
    const done = S.exportedIds.has(item.id);
    return `<tr class="file-row ${checked ? "is-selected " : ""}${done ? "is-done" : ""}" data-kind="file" data-id="${item.id}">
      <td class="col-check"><label class="check"><input type="checkbox" data-check="${item.id}" ${checked ? "checked" : ""} /><span></span></label></td>
      <td class="col-type"><span class="type-ico t-${family}"><svg><use href="#${FAMILY_ICONS[family]}"/></svg></span></td>
      <td class="col-name"><div class="row-name"><span class="fname" title="${htmlEscape(item.name)}">${htmlEscape(item.name)}</span>${done ? '<span class="tag-done">已导出</span>' : ""}</div></td>
      <td class="col-count"></td>
      <td class="col-size"><span class="size">${htmlEscape(fmtSize(item.size))}</span></td>
      <td class="col-time"><span class="mtime">${htmlEscape(fmtTime(item.mtime))}</span></td>
      <td class="col-act"><button class="icon-btn" data-act="reveal" title="在资源管理器中定位"><svg><use href="#i-locate"/></svg></button></td>
    </tr>`;
  }

  function entryHtml(entry) {
    return entry.kind === "folder" ? folderRowHtml(entry.node) : fileRowHtml(entry.item);
  }

  function renderRows() {
    const start = S.rendered;
    if (start >= S.view.length) {
      updateLoadMore();
      return;
    }
    const slice = S.view.slice(start, start + CHUNK);
    $("#tbody").insertAdjacentHTML("beforeend", slice.map(entryHtml).join(""));
    S.rendered += slice.length;
    syncRows();
    updateLoadMore();
  }

  function updateLoadMore() {
    const box = $("#load-more");
    const rest = S.view.length - S.rendered;
    box.hidden = rest <= 0;
    if (rest > 0) $("#btn-load-more").textContent = `加载更多（还有 ${rest.toLocaleString()} 项）`;
  }

  function updateEmptyState() {
    const empty = $("#empty-state");
    const resetBtn = $("#empty-reset");
    if (S.view.length) {
      empty.hidden = true;
      return;
    }
    empty.hidden = false;
    const filtering = Boolean(S.query) || S.typeFilter !== "all";

    if (!S.scanMeta) {
      $("#empty-title").textContent = "先选择一个文件夹开始扫描";
      $("#empty-sub").textContent = "选择源文件夹与时间范围后，这里会按子文件夹列出命中的文件。";
      if (resetBtn) resetBtn.hidden = true;
    } else if (!S.items.length) {
      $("#empty-title").textContent = "这个时间范围内没有符合条件的文件";
      $("#empty-sub").textContent = "可以换一个时间范围，或关闭扩展名/关键字过滤后重新扫描。";
      if (resetBtn) resetBtn.hidden = true;
    } else if (S.path.length) {
      $("#empty-title").textContent = "该文件夹下无匹配文件";
      $("#empty-sub").textContent = filtering
        ? "当前搜索或类型筛选没有命中；可返回上一级，或清空筛选条件。"
        : "这个文件夹里没有符合扫描条件的文件。";
      if (resetBtn) resetBtn.hidden = !filtering;
    } else {
      $("#empty-title").textContent = "没有符合当前筛选的文件";
      $("#empty-sub").textContent = "可以清空搜索或切换类型，也可以放宽时间范围后重新扫描。";
      if (resetBtn) resetBtn.hidden = false;
    }
  }

  function updateCheckAll() {
    const box = $("#check-all");
    const node = S.node;
    const total = node ? node.ids.size : 0;
    const picked = node ? node.picked : 0;
    box.disabled = total === 0;
    box.checked = total > 0 && picked >= total;
    box.indeterminate = picked > 0 && picked < total;
  }

  function applyCheckbox(row, checked, indeterminate) {
    const input = $("input[type=checkbox]", row);
    if (!input) return;
    input.checked = checked;
    input.indeterminate = indeterminate;
    row.classList.toggle("is-selected", checked);
    row.classList.toggle("is-partial", indeterminate);
  }

  function syncRows() {
    $$("#tbody tr").forEach((row) => {
      if (row.dataset.kind === "folder") {
        const node = nodeByPath(row.dataset.folder);
        const state = node ? folderState(node) : "none";
        applyCheckbox(row, state === "all", state === "partial");
      } else {
        const id = Number(row.dataset.id);
        const on = S.selected.has(id);
        applyCheckbox(row, on, false);
      }
    });
  }

  function refreshChecks() {
    syncRows();
    updateCheckAll();
    updateSelectionSummary();
  }

  function toggleFile(id, on) {
    const item = S.byId.get(id);
    if (on) {
      if (S.selected.has(id)) return;
      S.selected.add(id);
      if (item) S.selectedBytes += Number(item.size) || 0;
    } else {
      if (!S.selected.has(id)) return;
      S.selected.delete(id);
      if (item) S.selectedBytes -= Number(item.size) || 0;
    }
    let cur = item ? item.__node || null : null;
    while (cur) {
      cur.picked += on ? 1 : -1;
      cur = cur.parent;
    }
  }

  function toggleFolder(path, on) {
    const node = nodeByPath(path);
    if (!node) return;
    let delta = 0;
    node.ids.forEach((id) => {
      const item = S.byId.get(id);
      if (on) {
        if (S.selected.has(id)) return;
        S.selected.add(id);
        delta += 1;
        if (item) S.selectedBytes += Number(item.size) || 0;
      } else {
        if (!S.selected.has(id)) return;
        S.selected.delete(id);
        delta -= 1;
        if (item) S.selectedBytes -= Number(item.size) || 0;
      }
    });
    // 子树内选择状态已统一，直接写入 picked；祖先按增量修正
    walkNodes(node, (child) => {
      child.picked = on ? child.ids.size : 0;
    });
    let cur = node.parent;
    while (cur) {
      cur.picked += delta;
      cur = cur.parent;
    }
  }

  // 勾选当前所在文件夹的全部文件
  function toggleCurrent(on) {
    const node = S.node || S.tree;
    if (node) toggleFolder(node.path, on);
  }

  function enterFolder(path) {
    S.path = path ? path.split("\\") : [];
    if (!nodeAt(S.path)) S.path = [];
    S.node = nodeAt(S.path) || S.tree;
    S.view = currentEntries();
    S.rendered = 0;
    $("#tbody").innerHTML = "";
    renderRows();
    updateTableMode();
    updateCrumbs();
    updateEmptyState();
    updateCheckAll();
    updateActionState();
    $("#table-wrap").scrollTop = 0;
  }

  /* ---------------- 扫描 ---------------- */
  function setScanBusy(busy) {
    S.scanning = busy;
    $("#btn-scan-label").textContent = busy ? "正在扫描…" : "扫描文件";
    $("#scan-progress").hidden = !busy;
    updateActionState();
  }

  async function startScan() {
    const range = currentRange();
    if (range.invalid) {
      toast(range.label, "warn");
      return;
    }
    if (!S.settings.source) {
      toast("请先选择源文件夹", "warn");
      return;
    }
    resetResults();
    setScanBusy(true);
    setBar($("#scan-bar"), 0, true);
    $("#scan-text").textContent = "正在准备…";

    const payload = {
      root: S.settings.source,
      recursive: Boolean(S.settings.recursive),
      startMs: range.start ? range.start.getTime() : null,
      endMs: range.end ? range.end.getTime() : null,
      extensions: S.settings.extensions || "",
      keyword: S.settings.keyword || "",
    };
    try {
      const res = await api().start_scan(payload);
      if (!res.ok) {
        setScanBusy(false);
        toast(res.message || "无法开始扫描", "error");
        return;
      }
      persist();
      schedulePoll();
    } catch (err) {
      setScanBusy(false);
      toast(`扫描启动失败：${err}`, "error");
    }
  }

  function setBar(el, percent, indeterminate) {
    el.classList.toggle("is-indeterminate", Boolean(indeterminate));
    if (!indeterminate) el.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  }

  function scanProgressText(st) {
    if (st.phase === "枚举文件") return st.message || "正在遍历文件夹…";
    const total = Number(st.total) || 0;
    const done = Number(st.processed) || 0;
    const base = `${st.phase || "扫描"} ${done.toLocaleString()} / ${total.toLocaleString()}`;
    const current = st.current ? ` · ${st.current.split("\\").pop()}` : "";
    return `${base}${current}`;
  }

  function handleScanPoll(st) {
    if (!S.scanning) return;
    if (st.status === "running") {
      const indeterminate = st.total === 0 || st.phase === "枚举文件";
      setBar($("#scan-bar"), st.percent, indeterminate);
      $("#scan-text").textContent = scanProgressText(st);
      return;
    }
    S.scanning = false;
    $("#scan-progress").hidden = true;
    $("#btn-scan-label").textContent = "扫描文件";
    updateActionState();

    if (st.status === "done") {
      loadScanResult();
    } else if (st.status === "cancelled") {
      toast("已取消扫描", "warn");
      updateEmptyState();
    } else {
      toast(st.error || "扫描失败", "error");
      updateEmptyState();
    }
  }

  async function loadScanResult() {
    try {
      const res = await api().scan_result();
      if (!res.ok) {
        toast(res.message || "读取结果失败", "error");
        return;
      }
      S.scanMeta = res;
      S.items = res.items || [];
      S.byId = new Map(S.items.map((it) => [it.id, it]));
      S.selected.clear();
      S.selectedBytes = 0;
      S.exportedIds.clear();
      S.path = [];
      updateTypeFilterOptions();
      rebuildView();
      updateSelectionSummary();
      const count = S.items.length;
      toast(
        count
          ? `扫描完成，命中 ${count.toLocaleString()} 个文件`
          : "扫描完成，这个范围内没有文件",
        count ? "ok" : "warn",
      );
      if (res.error_count) {
        const tip = res.errors && res.errors.length ? `；例如：${res.errors[0].path}` : "";
        toast(`${res.error_count} 个文件夹无法访问${tip}`, "warn");
      }
    } catch (err) {
      toast(`读取扫描结果失败：${err}`, "error");
    }
  }

  /* ---------------- 导出 ---------------- */
  async function startExport() {
    if (!S.selected.size) {
      toast("请先勾选要导出的文件", "warn");
      return;
    }
    if (!S.settings.dest) {
      toast("请先选择导出文件夹", "warn");
      return;
    }
    const payload = {
      ids: Array.from(S.selected),
      dest: S.settings.dest,
      mode: S.settings.mode,
      preserve: Boolean(S.settings.preserve),
      conflict: S.settings.conflict,
    };
    S.exporting = true;
    $("#export-progress").hidden = false;
    $("#export-text").textContent = "正在准备…";
    setBar($("#export-bar"), 0, false);
    updateActionState();
    try {
      const res = await api().start_export(payload);
      if (!res.ok) {
        S.exporting = false;
        $("#export-progress").hidden = true;
        updateActionState();
        toast(res.message || "无法开始导出", "error");
        return;
      }
      persist();
      schedulePoll();
    } catch (err) {
      S.exporting = false;
      $("#export-progress").hidden = true;
      updateActionState();
      toast(`导出启动失败：${err}`, "error");
    }
  }

  function handleExportPoll(st) {
    if (!S.exporting) return;
    if (st.status === "running") {
      setBar($("#export-bar"), st.percent, st.total === 0);
      $("#export-text").textContent = `导出 ${Number(st.processed).toLocaleString()} / ${Number(st.total).toLocaleString()}${
        st.current ? ` · ${st.current.split("\\").pop()}` : ""
      }`;
      return;
    }
    S.exporting = false;
    updateActionState();
    setTimeout(loadExportResult, 120);
  }

  async function loadExportResult() {
    try {
      const res = await api().export_result();
      $("#export-progress").hidden = true;
      if (!res.ok) {
        toast(res.message || "读取导出结果失败", "error");
        return;
      }
      S.lastExport = res;
      // 只标记真正成功的文件：跳过（重名）与失败的不能打上“已导出”
      (res.exported_ids || []).forEach((id) => S.exportedIds.add(Number(id)));
      S.selected.clear();
      S.selectedBytes = 0;
      recountSelection();
      // 重绘当前层，刷新“已导出”标记与文件夹勾选状态
      S.rendered = 0;
      $("#tbody").innerHTML = "";
      renderRows();
      updateCheckAll();
      updateSelectionSummary();
      showExportModal(res);
    } catch (err) {
      $("#export-progress").hidden = true;
      toast(`读取导出结果失败：${err}`, "error");
    }
  }

  function showExportModal(res) {
    const modal = $("#modal");
    const mark = $("#modal-mark");
    mark.classList.remove("is-warn", "is-error");
    let title = "导出完成";
    if (res.cancelled) {
      title = "导出已取消";
      mark.classList.add("is-warn");
      mark.innerHTML = '<svg><use href="#i-warn"/></svg>';
    } else if (res.failed && !res.succeeded) {
      title = "导出失败";
      mark.classList.add("is-error");
      mark.innerHTML = '<svg><use href="#i-warn"/></svg>';
    } else {
      mark.innerHTML = '<svg><use href="#i-check"/></svg>';
    }
    $("#modal-title").textContent = title;
    const modeText = res.mode === "move" ? "剪切" : "复制";
    const parts = [`${modeText} ${res.processed} 个文件`];
    if (S.lastExport && S.lastExport.bytes) parts.push(`共 ${fmtSize(res.bytes)}`);
    if (res.duration_ms) parts.push(`用时 ${(res.duration_ms / 1000).toFixed(1)} 秒`);
    $("#modal-sub").textContent = parts.join(" · ");
    $("#stat-ok").textContent = res.succeeded;
    $("#stat-skip").textContent = res.skipped;
    $("#stat-fail").textContent = res.failed;

    const rowsBox = $("#modal-rows");
    const lines = [];
    (res.failures || []).slice(0, 4).forEach((f) => {
      lines.push(`失败：${htmlEscape(f.name)} — ${htmlEscape(f.error)}`);
    });
    if (res.cancelled) lines.push("任务被取消，已完成的部分保留在目标文件夹中。");
    rowsBox.innerHTML = lines.map((line) => `<div>${line}</div>`).join("");
    rowsBox.classList.toggle("is-visible", lines.length > 0);

    $("#btn-open-dest").disabled = false;
    modal.hidden = false;
    $("#btn-modal-done").focus();
  }

  /* ---------------- 轮询 ---------------- */
  function schedulePoll() {
    clearTimeout(S.pollTimer);
    S.pollTimer = setTimeout(pollTick, POLL_MS);
  }

  async function pollTick() {
    if (!S.scanning && !S.exporting) return;
    let data = null;
    try {
      data = await api().poll();
    } catch (err) {
      // 窗口关闭等场景，静默停止
      S.scanning = false;
      S.exporting = false;
      return;
    }
    if (data) {
      if (data.scan) handleScanPoll(data.scan);
      if (data.export) handleExportPoll(data.export);
    }
    if (S.scanning || S.exporting) schedulePoll();
  }

  /* ---------------- 设置持久化 ---------------- */
  const persist = debounce(() => {
    if (!window.pywebview || !window.pywebview.api) return;
    const payload = {
      source: S.settings.source,
      dest: S.settings.dest,
      preset: S.settings.preset,
      customStart: S.settings.customStart,
      customEnd: S.settings.customEnd,
      recursive: Boolean(S.settings.recursive),
      extensions: S.settings.extensions || "",
      keyword: S.settings.keyword || "",
      mode: S.settings.mode,
      preserve: Boolean(S.settings.preserve),
      conflict: S.settings.conflict,
      sort: S.sort,
      typeFilter: S.typeFilter,
      search: $("#search-input").value || "",
    };
    api().save_settings(payload).catch(() => {});
  }, 350);

  /* ---------------- Toast ---------------- */
  function toast(message, type = "info") {
    const icon = type === "ok" ? "i-check" : type === "error" ? "i-warn" : type === "warn" ? "i-warn" : "i-info";
    const el = document.createElement("div");
    el.className = `toast is-${type}`;
    el.innerHTML = `<svg><use href="#${icon}"/></svg><span>${htmlEscape(message)}</span>`;
    $("#toasts").appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .2s ease, transform .2s ease";
      el.style.opacity = "0";
      el.style.transform = "translateY(6px)";
      setTimeout(() => el.remove(), 220);
    }, 3800);
  }

  /* ---------------- 浏览器降级模式 ---------------- */
  async function promptFolder(kind) {
    const current = kind === "source" ? S.settings.source : S.settings.dest;
    const value = window.prompt(
      kind === "source" ? "浏览器模式：请输入源文件夹的绝对路径" : "浏览器模式：请输入导出文件夹的绝对路径",
      current || ""
    );
    if (value === null) return;
    const path = value.trim().replace(/^"|"$/g, "");
    if (!path) return;
    try {
      const check = await api().check_path(path);
      if (!check || !check.ok || !check.exists) {
        toast("路径不存在或不是文件夹，请检查后重试", "warn");
        return;
      }
    } catch (err) {
      toast(`无法校验路径：${err}`, "error");
      return;
    }
    if (kind === "source") {
      setSource(path);
      toast("已更新源文件夹，请重新扫描", "info");
    } else {
      setDest(path);
      toast("已设置导出文件夹", "ok");
    }
  }

  function setupBrowserMode(info) {
    S.browserMode = true;
    const banner = $("#browser-banner");
    banner.hidden = false;
    $("#browser-banner-text").textContent = info.modeReason || "已切换到浏览器模式";
    $("#webview2-download").href = info.downloadUrl || "https://go.microsoft.com/fwlink/p/?LinkId=2124703";
    $("#browser-banner-close").addEventListener("click", () => {
      banner.hidden = true;
    });
    $("#btn-pick-source").textContent = "输入路径";
    $("#btn-pick-dest").textContent = "输入路径";
    if (!S.settings.source) $("#dz-title").textContent = "浏览器模式：点「输入路径」填写文件夹";
    const engine = $("#about-engine");
    if (engine) engine.textContent = "系统浏览器（未使用 WebView2）";
  }

  /* ---------------- 事件绑定 ---------------- */
  function bindUI() {
    $("#btn-pick-source").addEventListener("click", async () => {
      const res = await api().pick_folder("source");
      if (res && res.browser) {
        promptFolder("source");
        return;
      }
      if (res.ok) {
        setSource(res.path);
        toast("已更新源文件夹，请重新扫描", "info");
      }
    });
    $("#btn-pick-dest").addEventListener("click", async () => {
      const res = await api().pick_folder("dest");
      if (res && res.browser) {
        promptFolder("dest");
        return;
      }
      if (res.ok) {
        setDest(res.path);
        toast("已设置导出文件夹", "ok");
      }
    });

    $("#preset-grid").addEventListener("click", (e) => {
      const btn = e.target.closest(".preset");
      if (!btn) return;
      S.settings.preset = btn.dataset.preset;
      if (S.settings.preset === "custom") {
        if (!S.settings.customStart || !S.settings.customEnd) {
          const today = startOfDay();
          const weekAgo = new Date(today);
          weekAgo.setDate(weekAgo.getDate() - 6);
          S.settings.customStart = fmtDate(weekAgo);
          S.settings.customEnd = fmtDate(today);
          $("#date-start").value = S.settings.customStart;
          $("#date-end").value = S.settings.customEnd;
        }
      }
      updatePresetUI();
      persist();
    });

    $("#date-start").addEventListener("change", (e) => {
      S.settings.customStart = e.target.value;
      S.settings.preset = "custom";
      updatePresetUI();
      persist();
    });
    $("#date-end").addEventListener("change", (e) => {
      S.settings.customEnd = e.target.value;
      S.settings.preset = "custom";
      updatePresetUI();
      persist();
    });

    $("#opt-recursive").addEventListener("change", (e) => {
      S.settings.recursive = e.target.checked;
      persist();
    });
    $("#opt-extensions").addEventListener("input", (e) => {
      S.settings.extensions = e.target.value;
      persist();
    });
    $("#opt-keyword").addEventListener("input", (e) => {
      S.settings.keyword = e.target.value;
      persist();
    });

    $("#btn-scan").addEventListener("click", startScan);
    $("#btn-cancel-scan").addEventListener("click", async () => {
      await api().cancel("scan");
      toast("正在取消扫描…", "info");
    });

    $("#search-input").addEventListener("input", debounce(rebuildView, 180));
    $("#type-filter").addEventListener("change", rebuildView);
    $("#sort-select").addEventListener("change", () => {
      S.sort = $("#sort-select").value;
      rebuildView();
      persist();
    });

    $("#btn-up").addEventListener("click", () => {
      if (!S.path.length) return;
      enterFolder(S.path.slice(0, -1).join("\\"));
    });

    $("#crumbs").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-crumb]");
      if (!btn) return;
      enterFolder(S.path.slice(0, Number(btn.dataset.crumb)).join("\\"));
    });

    $("#check-all").addEventListener("change", (e) => {
      toggleCurrent(e.target.checked);
      refreshChecks();
    });

    $("#tbody").addEventListener("change", (e) => {
      const input = e.target;
      if (!input || input.type !== "checkbox") return;
      if (input.hasAttribute("data-fcheck")) {
        toggleFolder(input.dataset.fcheck, input.checked);
        refreshChecks();
      } else if (input.hasAttribute("data-check")) {
        toggleFile(Number(input.dataset.check), input.checked);
        refreshChecks();
      }
    });

    $("#tbody").addEventListener("click", (e) => {
      const row = e.target.closest("tr");
      if (!row) return;
      const openBtn = e.target.closest("[data-open]");
      if (openBtn) {
        enterFolder(openBtn.dataset.open);
        return;
      }
      const action = e.target.closest("[data-act='reveal']");
      if (action) {
        const item = S.byId.get(Number(row.dataset.id));
        if (!item) return;
        api().reveal(item.path).then((res) => {
          if (res && res.ok === false) toast(res.message, "error");
        });
        return;
      }
      if (e.target.closest(".check")) return;   // 勾选交给 change 事件

      if (row.dataset.kind === "folder") {
        enterFolder(row.dataset.folder);
        return;
      }
      const id = Number(row.dataset.id);
      if (!id) return;
      const on = !S.selected.has(id);
      toggleFile(id, on);
      refreshChecks();
    });

    $("#btn-load-more").addEventListener("click", renderRows);

    $("#table-wrap").addEventListener("scroll", () => {
      const wrap = $("#table-wrap");
      if (S.rendered >= S.view.length) return;
      if (wrap.scrollTop + wrap.clientHeight >= wrap.scrollHeight - 40) renderRows();
    });

    $("#btn-select-all").addEventListener("click", () => {
      toggleCurrent(true);
      refreshChecks();
    });

    $("#btn-clear-selection").addEventListener("click", () => {
      S.selected.clear();
      recountSelection();
      refreshChecks();
    });

    $("#mode-seg").addEventListener("click", (e) => {
      const btn = e.target.closest(".seg");
      if (!btn) return;
      S.settings.mode = btn.dataset.mode;
      updateActionState();
      persist();
    });

    $("#opt-preserve").addEventListener("change", (e) => {
      S.settings.preserve = e.target.checked;
      persist();
    });
    $("#opt-conflict").addEventListener("change", (e) => {
      S.settings.conflict = e.target.value;
      persist();
    });

    $("#btn-export").addEventListener("click", startExport);
    $("#btn-cancel-export").addEventListener("click", async () => {
      await api().cancel("export");
      toast("正在取消导出…", "info");
    });

    $("#btn-modal-done").addEventListener("click", () => {
      $("#modal").hidden = true;
    });
    $("#modal-close").addEventListener("click", () => {
      $("#modal").hidden = true;
    });
    $("#btn-open-dest").addEventListener("click", () => {
      if (!S.settings.dest) return;
      api().open_path(S.settings.dest).then((res) => {
        if (res && res.ok === false) toast(res.message, "error");
      });
    });
    $("#about-chip").addEventListener("click", () => {
      $("#about-modal").hidden = false;
      $("#about-done").focus();
    });
    $("#about-close").addEventListener("click", () => ($("#about-modal").hidden = true));
    $("#about-done").addEventListener("click", () => ($("#about-modal").hidden = true));

    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      $("#modal").hidden = true;
      $("#about-modal").hidden = true;
    });

    // 拖拽
    const dz = $("#drop-zone");
    ["dragenter", "dragover"].forEach((evt) => {
      dz.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dz.classList.add("is-dragover");
      });
    });
    ["dragleave", "dragend"].forEach((evt) => {
      dz.addEventListener(evt, (e) => {
        e.preventDefault();
        if (e.relatedTarget && dz.contains(e.relatedTarget)) return;
        dz.classList.remove("is-dragover");
      });
    });
    document.addEventListener("dragover", (e) => e.preventDefault());
    dz.addEventListener("drop", (e) => {
      e.preventDefault();
      dz.classList.remove("is-dragover");
      if (S.browserMode) {
        toast("浏览器模式不支持拖拽，请点「输入路径」手动填写", "warn");
        return;
      }
      if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
      S.pendingDrop = true;
      setTimeout(() => {
        if (!S.pendingDrop) return;
        S.pendingDrop = false;
        toast("未能读取拖入路径，请改用「选择文件夹」按钮", "warn");
      }, 600);
    });

    // 供 Python 拖拽回调调用
    window.__tfpDrop = (paths) => {
      if (!paths || !paths.length) return;
      S.pendingDrop = false;
      const first = paths.find((p) => p.isDir) || paths[0];
      const dir = first.isDir ? first.path : first.dir;
      setSource(dir);
      if (!first.isDir && first.name) {
        S.settings.keyword = first.name;
        $("#opt-keyword").value = first.name;
        persist();
        toast(`已选择所在文件夹，并按文件名「${first.name}」筛选`, "ok");
      } else {
        toast(paths.length > 1 ? `已使用第一个文件夹：${first.path}` : `已选择文件夹：${first.path}`, "ok");
      }
    };

    $("#empty-state").addEventListener("click", (e) => {
      if (!e.target.closest("#empty-reset")) return;
      $("#search-input").value = "";
      $("#type-filter").value = "all";
      rebuildView();
      persist();
    });
  }

  /* ---------------- 初始化 ---------------- */
  function applySettings(settings) {
    S.settings = Object.assign({}, settings);
    $("#opt-recursive").checked = Boolean(S.settings.recursive);
    $("#opt-extensions").value = S.settings.extensions || "";
    $("#opt-keyword").value = S.settings.keyword || "";
    $("#sort-select").value = S.settings.sort || "mtime_desc";
    S.sort = $("#sort-select").value;
    $("#search-input").value = S.settings.search || "";
    S.typeFilter = S.settings.typeFilter || "all";
    if (!S.settings.preset) S.settings.preset = "week";

    if (!S.settings.customStart || !S.settings.customEnd) {
      const today = startOfDay();
      const weekAgo = new Date(today);
      weekAgo.setDate(weekAgo.getDate() - 6);
      S.settings.customStart = S.settings.customStart || fmtDate(weekAgo);
      S.settings.customEnd = S.settings.customEnd || fmtDate(today);
    }
    $("#date-start").value = S.settings.customStart;
    $("#date-end").value = S.settings.customEnd;

    if (!["copy", "move"].includes(S.settings.mode)) S.settings.mode = "copy";
    if (!["skip", "rename", "overwrite"].includes(S.settings.conflict)) S.settings.conflict = "rename";
    S.settings.preserve = Boolean(S.settings.preserve);

    setSource(S.settings.source || "");
    setDest(S.settings.dest || "");
    updatePresetUI();
  }

  async function boot() {
    let info = null;
    try {
      info = await api().bootstrap();
    } catch (err) {
      toast("无法连接程序内核，请重新启动程序", "error");
      return;
    }
    if (!info || !info.ok) return;
    S.boot = info;
    applySettings(info.settings || {});
    if (info.browserMode) setupBrowserMode(info);
    updateActionState();
    updateSelectionSummary();
    updateEmptyState();
    updateResultHeader();
    updateCrumbs();
    $("#about-version").textContent = `v${info.version || "1.0.0"}`;
    $("#about-config").textContent = info.settingsPath || "—";
    $("#about-config").title = info.settingsPath || "";
  }

  function addEmptyReset() {
    const empty = $("#empty-state");
    const btn = document.createElement("button");
    btn.id = "empty-reset";
    btn.className = "btn btn-soft btn-small empty-reset";
    btn.hidden = true;
    btn.textContent = "清除筛选条件";
    empty.appendChild(btn);
  }

  function start() {
    S.tree = makeNode("", "");
    S.node = S.tree;
    S.view = [];
    addEmptyReset();
    bindUI();
    updateCrumbs();
    if (window.pywebview && window.pywebview.api) boot();
    else window.addEventListener("pywebviewready", boot, { once: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
