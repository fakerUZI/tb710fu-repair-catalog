const SYMBOL_LANGUAGE = 'tbSymbolLanguage';
const CHINESE_PUNCTUATION = new Map([
    [',', ['，']], ['.', ['。', '……']], [';', ['；']], [':', ['：']],
    ['!', ['！']], ['?', ['？']], ['/', ['、', '／']], ['~', ['～']],
    ['(', ['（']], [')', ['）']], ['[', ['【', '［']], [']', ['】', '］']],
    ['{', ['｛']], ['}', ['｝']], ['<', ['《', '〈']], ['>', ['》', '〉']],
    ['"', ['“', '”']], ["'", ['‘', '’']],
]);

function useChinesePunctuation(keys) {
    return keys.map(key => {
        const punctuation = CHINESE_PUNCTUATION.get(key.strings?.[0]);
        return punctuation
            ? {...key, strings: [...new Set([...punctuation, ...key.strings])]}
            : key;
    });
}

function leadWithSymbols(keys, symbols) {
    const preferred = symbols.map(symbol => keys.find(key => key.strings?.[0] === symbol));
    return preferred.every(Boolean)
        ? [...preferred, ...keys.filter(key => !preferred.includes(key))]
        : keys;
}

function arrangeAdvancedSymbols(keys) {
    const ordered = leadWithSymbols(keys, ['[', ']', '\\', '©', '®', '™', '℅']);
    if (ordered === keys || ordered.length !== 7)
        return keys;
    const [left, right, slash, ...marks] = ordered;
    return [left, right, slash, {strings: ['≤']}, {strings: ['≥']}, {strings: ['≠']},
        {...marks[0], strings: marks.flatMap(key => key.strings)}];
}

function alignSymbolRow(keys, shiftKeyval, chineseSymbols) {
    const first = keys[0];
    const last = keys.at(-1);
    if (keys.length === 11 && last.action === 'delete') {
        const symbols = leadWithSymbols(keys.slice(0, -1), ['|', '`', '~']);
        return [{label: 'Tab', keyval: '0xff09'}, ...symbols, {...last, width: 2}];
    }
    if (keys.length === 10 && last.iconName === 'osk-enter-symbolic') {
        // The comma moves here from the space row, keeping punctuation full-size.
        const symbols = leadWithSymbols(
            [{...first, leftOffset: 0}, ...keys.slice(1, -1)], ['{', '}']);
        return [{label: chineseSymbols ? '英符' : '中符', action: SYMBOL_LANGUAGE},
            ...symbols, {strings: [',']}, last];
    }
    if (keys.length === 9 && ['opt', 'opt+shift'].includes(first.level) &&
        first.action === 'levelSwitch' && last.action === 'levelSwitch' && first.level === last.level) {
        const advancedPage = last.level === 'opt';
        const symbols = keys.slice(1, -1);
        const pageSwitch = advancedPage ? '⇧ ?123' : chineseSymbols ? '⇧ 【】|' : '⇧ []{}|';
        // The right Shift position changes pages. The left Shift still works for shortcuts.
        return [{label: 'Shift', action: 'modifier', keyval: shiftKeyval, width: 2},
            ...(advancedPage ? arrangeAdvancedSymbols(symbols) : symbols), {strings: ['.', '…']},
            {iconName: 'go-up-symbolic', keyval: '0xff52'}, {...last, label: pageSwitch, width: 2}];
    }
    const [, left, right, space, comma, period, emoji, language, hide] = keys;
    if (keys.length === 9 && first.action === 'levelSwitch' && first.level === 'default' &&
        space.strings?.[0] === ' ' && comma.strings?.[0] === ',' && period.strings?.[0] === '.' &&
        emoji.action === 'emoji' && language.action === 'languageMenu' && hide.action === 'hide') {
        const symbol = left.strings?.[0] === '<' ? '±' : '=';
        return [
            {label: 'Ctrl', keyval: '0xffe3', action: 'modifier'},
            {...first, width: 1},
            {label: 'Alt', keyval: '0xffe9', action: 'modifier'},
            left,
            {...space, width: 2},
            right,
            emoji,
            {strings: [symbol]},
            {iconName: 'go-previous-symbolic', keyval: '0xff51'},
            {iconName: 'go-down-symbolic', keyval: '0xff54'},
            {iconName: 'go-next-symbolic', keyval: '0xff53'},
            hide,
        ];
    }
    return keys;
}

// Use the extended layout in ordinary text fields as well as terminals.
// Keep its upper Caps key and replace only the lower-left Shift.
// Native modifier registration supplies touch handling and the latched state.
export class TerminalShiftModifier {
    constructor(keyboard, shiftKeyval, terminalPurpose, onKeysAdded, textPurposes = [], inputMethod = null) {
        this._keyboard = keyboard;
        this._shift = shiftKeyval;
        this._controller = keyboard._keyboardController;
        this._inputMethod = inputMethod;
        this._injected = new Set();
        this._down = false;
        this._chineseSymbols = false;
        this._chineseText = false;
        this._textPunctuation = new Map();
        const extension = this;
        this._setActiveLevel = keyboard._setActiveLevel;
        keyboard._setActiveLevel = function (level) {
            // A late capitalization reply from a text field can arrive after
            // switching to a one-level numeric model. Never select a missing page.
            const target = this._layers?.[level] ? level : 'default';
            if (!this._layers?.[target])
                return;
            return extension._setActiveLevel.call(this, target);
        };
        this._updateLayout = keyboard._updateLayout;
        const extendedPurposes = new Set([terminalPurpose, ...textPurposes]);
        keyboard._updateLayout = function (group, purpose) {
            extension._buildingExtendedLayout = extendedPurposes.has(purpose);
            try {
                return extension._updateLayout.call(this, group,
                    extension._buildingExtendedLayout ? terminalPurpose : purpose);
            } finally {
                extension._buildingExtendedLayout = false;
            }
        };
        this._addRowKeys = keyboard._addRowKeys;
        keyboard._addRowKeys = function (keys, layout, emojiVisible) {
            const first = keys[0];
            const extended = this._keyboardController.purpose === terminalPurpose || extension._buildingExtendedLayout;
            const textPage = extended && (layout.mode === 'default' || layout.mode === 'latched');
            if (extended &&
                first?.action === 'levelSwitch' &&
                first.iconName === 'osk-shift-symbolic') {
                keys = [{action: 'modifier', keyval: shiftKeyval,
                    label: 'Shift', width: first.width, height: first.height,
                    leftOffset: first.leftOffset}, ...keys.slice(1)];
            }
            if (layout.mode === 'locked') {
                const aligned = alignSymbolRow(keys, shiftKeyval, extension._chineseSymbols);
                keys = aligned !== keys && extension._chineseSymbols
                    ? useChinesePunctuation(aligned)
                    : aligned;
            }
            if (textPage) {
                keys = keys.map(key => key.action === 'emoji'
                    ? {strings: [','], width: key.width, height: key.height, leftOffset: key.leftOffset}
                    : key);
            }
            extension._addRowKeys.call(this, keys, layout, emojiVisible);
            const actors = layout.get_children();
            if (textPage)
                extension._bindTextPunctuation(actors);
            if (keys[0]?.action === SYMBOL_LANGUAGE) {
                const toggle = actors.find(key => key.keyButton?.get_label() === keys[0].label);
                if (toggle) {
                    toggle.keyButton.accessible_name =
                        extension._chineseSymbols ? '切换到英文标点' : '切换到中文标点';
                    toggle.connect('released', () =>
                        extension._toggleSymbolLanguage(layout).catch(error => console.error(error)));
                }
            }
            for (const key of actors) {
                if (key.keyButton?.get_label() === 'Shift') {
                    key.keyButton.add_style_class_name('tb-osk-shift-modifier');
                    key.keyButton.accessible_name = 'Shift 组合键，点按开启或取消';
                }
            }
            onKeysAdded?.(layout);
        };
        this._updateKeys = keyboard._updateKeys;
        keyboard._updateKeys = function (...args) {
            extension._clear();
            if (this._surroundingTextId && inputMethod) {
                inputMethod.disconnect(this._surroundingTextId);
                this._surroundingTextId = 0;
            }
            // Discard registrations from the layout about to be destroyed.
            this._modifierKeys[shiftKeyval] = [];
            return extension._updateKeys.apply(this, args);
        };
        this._close = keyboard.close;
        keyboard.close = function (...args) {
            extension._clear();
            return extension._close.apply(this, args);
        };
        this._press = this._controller.keyvalPress;
        this._release = this._controller.keyvalRelease;
        this._controller.keyvalPress = keyval => this._keyPress(keyval);
        this._controller.keyvalRelease = keyval => this._keyRelease(keyval);
        keyboard._updateKeys();
    }
    setTextLanguage(chinese) {
        if (this._chineseText === chinese)
            return;
        this._chineseText = chinese;
        for (const [actor, {mark}] of this._textPunctuation)
            actor.keyButton.set_label(this._textMark(mark));
    }
    _textMark(mark) {
        return this._chineseText ? CHINESE_PUNCTUATION.get(mark)[0] : mark;
    }
    _bindTextPunctuation(actors) {
        for (const actor of actors) {
            const mark = actor.keyButton?.get_label();
            if ((mark !== ',' && mark !== '.') || this._textPunctuation.has(actor))
                continue;
            const release = actor._release;
            const destroyId = actor.connect('destroy', () => this._textPunctuation.delete(actor));
            this._textPunctuation.set(actor, {mark, release, destroyId});
            // Keep the native commit and long-press handling. Only the main key
            // takes its text from the current Fcitx language.
            actor._release = (button, text) => release.call(actor, button,
                button === actor.keyButton && text === mark ? this._textMark(mark) : text);
            actor.keyButton.set_label(this._textMark(mark));
        }
    }
    async _toggleSymbolLanguage(layout) {
        // Rebuild after the released signal has finished using its key actor.
        await Promise.resolve();
        const keyboard = this._keyboard;
        if (this._destroyed || keyboard._currentPage !== layout)
            return;
        const level = Object.keys(keyboard._layers).find(name => keyboard._layers[name] === layout);
        if (!level)
            return;
        this._chineseSymbols = !this._chineseSymbols;
        keyboard._updateKeys();
        keyboard._setActiveLevel(level);
    }
    _keyPress(keyval) {
        if (keyval === this._shift) {
            this._press.call(this._controller, keyval);
            this._down = true;
            return;
        }
        // GNOME forwards modifiers for text commits, but not raw arrow/Tab keys.
        if (this._keyboard._modifiers.has(this._shift) && !this._down) {
            this._press.call(this._controller, this._shift);
            this._down = true;
            this._injected.add(keyval);
        }
        try {
            this._press.call(this._controller, keyval);
        } catch (error) {
            this._releaseInjected(keyval);
            throw error;
        }
    }
    _keyRelease(keyval) {
        try {
            this._release.call(this._controller, keyval);
        } finally {
            if (keyval === this._shift)
                this._down = false;
            else
                this._releaseInjected(keyval);
        }
    }
    _releaseInjected(keyval) {
        if (!this._injected.delete(keyval))
            return;
        try {
            this._release.call(this._controller, this._shift);
        } finally {
            this._down = false;
        }
    }
    _clear() {
        if (this._keyboard._modifiers.has(this._shift))
            this._keyboard._setModifierEnabled(this._shift, false);
        if (this._injected.size)
            this._release.call(this._controller, this._shift);
        this._injected.clear();
        this._down = false;
    }
    destroy() {
        this._destroyed = true;
        for (const [actor, {mark, release, destroyId}] of this._textPunctuation) {
            actor._release = release;
            actor.keyButton.set_label(mark);
            actor.disconnect(destroyId);
        }
        this._textPunctuation.clear();
        this._clear();
        const keyboard = this._keyboard;
        if (keyboard._surroundingTextId && this._inputMethod) {
            this._inputMethod.disconnect(keyboard._surroundingTextId);
            keyboard._surroundingTextId = 0;
        }
        keyboard._addRowKeys = this._addRowKeys;
        keyboard._updateKeys = this._updateKeys;
        keyboard._updateLayout = this._updateLayout;
        keyboard._setActiveLevel = this._setActiveLevel;
        keyboard.close = this._close;
        this._controller.keyvalPress = this._press;
        this._controller.keyvalRelease = this._release;
        keyboard._modifierKeys[this._shift] = [];
        if (keyboard.get_parent())
            keyboard._updateKeys();
    }
}
