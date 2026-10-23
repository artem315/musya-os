declare module "@novnc/novnc" {
  export default class RFB extends EventTarget {
    constructor(target: HTMLElement, url: string, options?: { credentials?: { password?: string }; wsProtocols?: string[] });
    scaleViewport: boolean;
    resizeSession: boolean;
    viewOnly: boolean;
    focusOnClick: boolean;
    background: string;
    disconnect(): void;
    sendCtrlAltDel(): void;
    sendCredentials(c: { password: string }): void;
    focus(): void;
    clipboardPasteFrom(text: string): void;
  }
}
