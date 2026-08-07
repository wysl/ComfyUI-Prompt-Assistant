import { app } from "../../../../scripts/app.js";
import { APIService } from "../services/api.js";
import { logger } from "../utils/logger.js";


const styleLink = document.createElement("link");
styleLink.rel = "stylesheet";
styleLink.href = new URL("../css/referencePromptLibrary.css", import.meta.url).href;
document.head.appendChild(styleLink);


function parseSelection(value) {
    try {
        const parsed = JSON.parse(String(value || "[]"));
        return Array.isArray(parsed) ? [...new Set(parsed.map(String).filter(Boolean))] : [];
    } catch {
        return String(value || "")
            .split(/\r?\n/)
            .map(item => item.trim())
            .filter(Boolean);
    }
}


function updateButtonLabel(node) {
    const selectedWidget = node.widgets?.find(widget => widget.name === "selected_files");
    const count = parseSelection(selectedWidget?.value).length;
    if (node._paReferencePromptButton) {
        node._paReferencePromptButton.name = `📚 选择参考提示词（${count}）`;
    }
    node.setDirtyCanvas?.(true, true);
}


function makeIconButton(icon, title, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pa-rpl-icon-button";
    button.title = title;
    const iconElement = document.createElement("i");
    iconElement.className = `pi ${icon}`;
    button.appendChild(iconElement);
    button.addEventListener("click", onClick);
    return button;
}


function createBrowserModal(node, selectedWidget) {
    const selectedPaths = parseSelection(selectedWidget.value);
    let currentPath = "";
    let currentData = { directories: [], files: [] };
    let selectedView = false;
    let searchTerm = "";

    const overlay = document.createElement("div");
    overlay.className = "pa-rpl-overlay";

    const dialog = document.createElement("div");
    dialog.className = "pa-rpl-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-label", "多媒体参考提示词库");

    const header = document.createElement("div");
    header.className = "pa-rpl-header";
    const title = document.createElement("div");
    title.className = "pa-rpl-title";
    title.textContent = "多媒体参考提示词库";
    const headerActions = document.createElement("div");
    headerActions.className = "pa-rpl-header-actions";
    const refreshButton = makeIconButton("pi-refresh", "刷新当前目录", () => loadDirectory(currentPath));
    const closeButton = makeIconButton("pi-times", "关闭", close);
    headerActions.append(refreshButton, closeButton);
    header.append(title, headerActions);

    const toolbar = document.createElement("div");
    toolbar.className = "pa-rpl-toolbar";
    const breadcrumbs = document.createElement("div");
    breadcrumbs.className = "pa-rpl-breadcrumbs";
    const search = document.createElement("input");
    search.type = "search";
    search.className = "pa-rpl-search";
    search.placeholder = "搜索当前目录";
    search.addEventListener("input", () => {
        searchTerm = search.value.trim().toLocaleLowerCase();
        render();
    });
    toolbar.append(breadcrumbs, search);

    const content = document.createElement("div");
    content.className = "pa-rpl-content";

    const footer = document.createElement("div");
    footer.className = "pa-rpl-footer";
    const selectedToggle = document.createElement("button");
    selectedToggle.type = "button";
    selectedToggle.className = "pa-rpl-selected-toggle";
    selectedToggle.addEventListener("click", () => {
        selectedView = !selectedView;
        search.value = "";
        searchTerm = "";
        render();
    });
    const footerActions = document.createElement("div");
    footerActions.className = "pa-rpl-footer-actions";
    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.className = "pa-rpl-button pa-rpl-button-secondary";
    cancelButton.textContent = "取消";
    cancelButton.addEventListener("click", close);
    const confirmButton = document.createElement("button");
    confirmButton.type = "button";
    confirmButton.className = "pa-rpl-button pa-rpl-button-primary";
    confirmButton.textContent = "确定";
    confirmButton.addEventListener("click", () => {
        selectedWidget.value = JSON.stringify(selectedPaths);
        selectedWidget.callback?.(selectedWidget.value);
        node.graph?.setDirtyCanvas(true, true);
        updateButtonLabel(node);
        close();
    });
    footerActions.append(cancelButton, confirmButton);
    footer.append(selectedToggle, footerActions);

    dialog.append(header, toolbar, content, footer);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    function close() {
        document.removeEventListener("keydown", onKeyDown);
        overlay.remove();
    }

    function onKeyDown(event) {
        if (event.key === "Escape") close();
    }
    document.addEventListener("keydown", onKeyDown);
    overlay.addEventListener("mousedown", event => {
        if (event.target === overlay) close();
    });

    function setLoading() {
        content.replaceChildren();
        const loading = document.createElement("div");
        loading.className = "pa-rpl-empty";
        loading.textContent = "加载中...";
        content.appendChild(loading);
    }

    function showError(message) {
        content.replaceChildren();
        const error = document.createElement("div");
        error.className = "pa-rpl-error";
        error.textContent = message;
        content.appendChild(error);
    }

    async function loadDirectory(path) {
        selectedView = false;
        currentPath = path || "";
        search.value = "";
        searchTerm = "";
        setLoading();
        try {
            const url = new URL(APIService.getApiUrl("reference_prompts/list"));
            url.searchParams.set("path", currentPath);
            const response = await fetch(url);
            const data = await response.json();
            if (!response.ok || !data.success) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }
            currentData = data;
            currentPath = data.path || "";
            render();
        } catch (error) {
            logger.error(`加载参考提示词目录失败: ${error.message}`);
            showError(error.message || "目录加载失败");
        }
    }

    function renderBreadcrumbs() {
        breadcrumbs.replaceChildren();
        const root = document.createElement("button");
        root.type = "button";
        root.className = "pa-rpl-crumb";
        root.textContent = "根目录";
        root.addEventListener("click", () => loadDirectory(""));
        breadcrumbs.appendChild(root);

        let accumulated = "";
        for (const part of currentPath.split("/").filter(Boolean)) {
            const separator = document.createElement("span");
            separator.className = "pa-rpl-crumb-separator";
            separator.textContent = "/";
            breadcrumbs.appendChild(separator);
            accumulated = accumulated ? `${accumulated}/${part}` : part;
            const path = accumulated;
            const crumb = document.createElement("button");
            crumb.type = "button";
            crumb.className = "pa-rpl-crumb";
            crumb.textContent = part;
            crumb.addEventListener("click", () => loadDirectory(path));
            breadcrumbs.appendChild(crumb);
        }
    }

    function toggleFile(path) {
        const index = selectedPaths.indexOf(path);
        if (index >= 0) selectedPaths.splice(index, 1);
        else selectedPaths.push(path);
        render();
    }

    function createDirectoryRow(directory) {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "pa-rpl-row pa-rpl-directory";
        const icon = document.createElement("i");
        icon.className = "pi pi-folder";
        const label = document.createElement("span");
        label.textContent = directory.name;
        const arrow = document.createElement("i");
        arrow.className = "pi pi-chevron-right pa-rpl-row-arrow";
        row.append(icon, label, arrow);
        row.addEventListener("click", () => loadDirectory(directory.path));
        return row;
    }

    function createFileRow(file) {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "pa-rpl-row pa-rpl-file";
        const selected = selectedPaths.includes(file.path);
        if (selected) row.classList.add("selected");
        const checkbox = document.createElement("span");
        checkbox.className = "pa-rpl-checkbox";
        const icon = document.createElement("i");
        icon.className = "pi pi-file";
        const label = document.createElement("span");
        label.textContent = file.name;
        row.append(checkbox, icon, label);
        row.addEventListener("click", () => toggleFile(file.path));
        return row;
    }

    function moveSelection(index, offset) {
        const target = index + offset;
        if (target < 0 || target >= selectedPaths.length) return;
        [selectedPaths[index], selectedPaths[target]] = [selectedPaths[target], selectedPaths[index]];
        render();
    }

    function renderSelected() {
        content.replaceChildren();
        const filtered = selectedPaths
            .map((path, index) => ({ path, index }))
            .filter(item => !searchTerm || item.path.toLocaleLowerCase().includes(searchTerm));
        if (!filtered.length) {
            const empty = document.createElement("div");
            empty.className = "pa-rpl-empty";
            empty.textContent = selectedPaths.length ? "当前搜索没有结果" : "尚未选择文件";
            content.appendChild(empty);
            return;
        }
        for (const item of filtered) {
            const row = document.createElement("div");
            row.className = "pa-rpl-selected-row";
            const order = document.createElement("span");
            order.className = "pa-rpl-order";
            order.textContent = String(item.index + 1);
            const path = document.createElement("span");
            path.className = "pa-rpl-selected-path";
            path.textContent = item.path;
            const actions = document.createElement("div");
            actions.className = "pa-rpl-selected-actions";
            actions.append(
                makeIconButton("pi-chevron-up", "上移", () => moveSelection(item.index, -1)),
                makeIconButton("pi-chevron-down", "下移", () => moveSelection(item.index, 1)),
                makeIconButton("pi-trash", "移除", () => {
                    selectedPaths.splice(item.index, 1);
                    render();
                }),
            );
            row.append(order, path, actions);
            content.appendChild(row);
        }
    }

    function renderDirectory() {
        content.replaceChildren();
        const directories = (currentData.directories || []).filter(
            item => !searchTerm || item.name.toLocaleLowerCase().includes(searchTerm),
        );
        const files = currentPath
            ? (currentData.files || []).filter(
                item => !searchTerm || item.name.toLocaleLowerCase().includes(searchTerm),
            )
            : [];
        for (const directory of directories) content.appendChild(createDirectoryRow(directory));
        for (const file of files) content.appendChild(createFileRow(file));
        if (!directories.length && !files.length) {
            const empty = document.createElement("div");
            empty.className = "pa-rpl-empty";
            empty.textContent = currentPath ? "当前目录没有 TXT 文件或子目录" : "尚未创建提示词目录";
            content.appendChild(empty);
        }
    }

    function render() {
        selectedToggle.textContent = selectedView
            ? "返回目录"
            : `已选 ${selectedPaths.length} 项`;
        search.placeholder = selectedView ? "搜索已选文件" : "搜索当前目录";
        toolbar.classList.toggle("selected-view", selectedView);
        if (!selectedView) renderBreadcrumbs();
        else breadcrumbs.replaceChildren();
        if (selectedView) renderSelected();
        else renderDirectory();
    }

    loadDirectory("");
}


app.registerExtension({
    name: "ComfyUI.PromptAssistant.ReferencePromptLibrary",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "MultimediaReferencePromptLibraryNode") return;

        const originalCreated = nodeType.prototype.onNodeCreated;
        const originalConfigured = nodeType.prototype.onConfigure;

        nodeType.prototype.onNodeCreated = function () {
            originalCreated?.apply(this, arguments);
            if (this._paReferencePromptButton) return;

            const selectedWidget = this.widgets?.find(widget => widget.name === "selected_files");
            if (!selectedWidget) return;
            selectedWidget.hidden = true;
            selectedWidget.computeSize = () => [0, 0];

            this._paReferencePromptButton = this.addWidget(
                "button",
                "📚 选择参考提示词（0）",
                null,
                () => createBrowserModal(this, selectedWidget),
            );
            this.setSize?.([Math.max(this.size?.[0] || 0, 300), Math.max(this.size?.[1] || 0, 110)]);
            setTimeout(() => updateButtonLabel(this), 0);
        };

        nodeType.prototype.onConfigure = function () {
            originalConfigured?.apply(this, arguments);
            setTimeout(() => updateButtonLabel(this), 0);
        };
    },
});
