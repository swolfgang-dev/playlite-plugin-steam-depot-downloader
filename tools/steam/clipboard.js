// Transfer text only on an explicit paste gesture. Never log clipboard contents.
import UI from './ui.js';

let pasting = false;
function pasteText(text) {
    if (!text || !UI.rfb || UI.rfb.viewOnly) return;
    UI.rfb.clipboardPasteFrom(text);
    UI.rfb.sendKey(0xffe3, 'ControlLeft', true);
    UI.rfb.sendKey(0x76, 'KeyV');
    UI.rfb.sendKey(0xffe3, 'ControlLeft', false);
}

async function pasteClipboard() {
    if (pasting || !UI.rfb || UI.rfb.viewOnly) return;
    pasting = true;
    try {
        const text = await navigator.clipboard.readText();
        pasteText(text);
    } catch {
        UI.showStatus('Clipboard access was denied. Allow clipboard access or use the clipboard side panel.', 'warn');
    } finally {
        pasting = false;
    }
}

let pasteTarget;
document.addEventListener('keydown', event => {
    const screen = document.getElementById('noVNC_container');
    if (!screen?.contains(event.target) || !(event.ctrlKey || event.metaKey) ||
        event.shiftKey || event.altKey || event.code !== 'KeyV') return;
    if (!pasteTarget || !UI.rfb || UI.rfb.viewOnly) return;
    // Let the browser perform a real user-initiated paste into our offscreen
    // input. Unlike readText(), this does not require Firefox's Paste prompt.
    event.stopImmediatePropagation();
    pasteTarget.focus({preventScroll:true});
}, true);

function addPasteButton() {
    pasteTarget = document.createElement('textarea');
    pasteTarget.tabIndex = -1;
    pasteTarget.setAttribute('aria-label', 'Remote desktop clipboard paste');
    pasteTarget.style.cssText = 'position:fixed;left:-10000px;top:0;width:1px;height:1px;opacity:0';
    pasteTarget.addEventListener('paste', event => {
        event.preventDefault();
        pasteText(event.clipboardData?.getData('text/plain') || '');
        pasteTarget.value = '';
        UI.rfb?.focus();
    });
    document.body.appendChild(pasteTarget);
    const button = document.createElement('button');
    button.textContent = 'Paste clipboard';
    button.title = 'Paste text into the focused field in Steam';
    button.style.cssText = 'position:fixed;top:8px;right:8px;z-index:1000;padding:8px 12px;background:#30343b;color:white;border:1px solid #73777e;border-radius:5px;cursor:pointer';
    button.addEventListener('click', pasteClipboard);
    document.body.appendChild(button);
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', addPasteButton, {once:true});
} else {
    addPasteButton();
}
