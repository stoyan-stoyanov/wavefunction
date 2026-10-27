// The parts of noVNC's RFB client that the desktop takeover uses; the package ships no types.
declare module "@novnc/novnc" {
  export default class RFB extends EventTarget {
    constructor(target: HTMLElement, urlOrChannel: string | WebSocket, options?: { shared?: boolean });
    scaleViewport: boolean;
    focusOnClick: boolean;
    background: string;
    focus(): void;
    disconnect(): void;
    sendKey(keysym: number, code: string | null, down?: boolean): void;
  }
}
