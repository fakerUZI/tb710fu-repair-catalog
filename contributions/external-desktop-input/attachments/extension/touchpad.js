// Track touch sequences independently. A scroll or cancelled stroke must
// never turn into a click when the last finger lifts.
export class Touchpad {
    constructor(move, click, scroll, finishScroll) {
        Object.assign(this, {move, click, scroll, finishScroll});
        this.points = new Map();
    }

    begin(id, x, y, time) {
        if (!this.points.size) {
            this.tap = true;
            this.started = time;
        } else {
            this.tap = false;
        }
        this.points.set(id, {x, y, startX: x, startY: y});
    }

    update(id, x, y) {
        const p = this.points.get(id);
        if (!p)
            return;
        const dx = x - p.x;
        const dy = y - p.y;
        p.x = x;
        p.y = y;
        if (this.points.size === 2) {
            this.scrolling = true;
            this.scroll(-dx / 2, -dy / 2);
        } else if (this.points.size === 1 && !this.multi) {
            if (Math.hypot(x - p.startX, y - p.startY) > 7)
                this.tap = false;
            if (!this.tap)
                this.move(dx, dy);
        }
    }

    end(id, x, y, time, cancelled) {
        const p = this.points.get(id);
        if (!p)
            return;
        if (this.points.size > 1)
            this.multi = true;
        this.points.delete(id);
        if (this.scrolling && this.points.size < 2) {
            this.finishScroll();
            this.scrolling = false;
        }
        if (!this.points.size) {
            if (!cancelled && this.tap && time - this.started < 450 &&
                Math.hypot(x - p.startX, y - p.startY) <= 7)
                this.click();
            this.multi = false;
        }
    }

    reset() {
        if (this.scrolling)
            this.finishScroll();
        this.points.clear();
        this.scrolling = this.tap = this.multi = false;
    }
}
