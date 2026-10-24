using Godot;
using System;
using System.Collections.Generic;
using System.Linq;

// Model dialogue overlay: thinking / speech / turn-summary bubbles with a
// portrait in the bottom-right corner. Text arrives either as live host
// "stream" messages (LLM turns) or as synthesized beat playback; both go
// through the same typewriter queue. Pure presentation, no rule meaning.
public partial class SpeechBubbles : Control
{
    private PanelContainer _panel = null!;
    private Label _tag = null!, _title = null!, _portraitName = null!, _stats = null!;
    private RichTextLabel _thinking = null!, _body = null!;
    private PanelContainer _thinkingPanel = null!, _bodyPanel = null!;
    private Control _portrait = null!;
    private PanelContainer _logoFrame = null!;
    private TextureRect? _logo;
    private Label _badge = null!;
    private VBoxContainer _badgeBox = null!;
    private readonly Dictionary<string, (string Pending, int Shown)> _queues = new();
    private string _kind = "body", _actor = "";
    private string? _contextText;
    private int? _tokenCount;
    private double? _turnSeconds;
    private float _speed = 1;
    private double _holdClock = double.PositiveInfinity;
    private StyleBoxFlat _bodyStyle = null!, _summaryStyle = null!, _thinkingStyle = null!;
    public int SegmentsShown { get; private set; }
    public bool EverShown { get; private set; }
    public bool Streaming => _queues.TryGetValue(_kind, out var q) && q.Shown < q.Pending.Length;

    public override void _Ready()
    {
        MouseFilter = MouseFilterEnum.Ignore;
        ClipContents = false;
        _panel = new PanelContainer { MouseFilter = MouseFilterEnum.Ignore };
        _panel.SetAnchorsAndOffsetsPreset(LayoutPreset.CenterBottom);
        _panel.OffsetLeft = -390; _panel.OffsetRight = 390; _panel.OffsetTop = -212; _panel.OffsetBottom = -18;
        var frame = new StyleBoxFlat { BgColor = new Color(.06f, .10f, .11f, .93f), BorderColor = new Color("5a6a58"),
            ContentMarginLeft = 14, ContentMarginRight = 14, ContentMarginTop = 8, ContentMarginBottom = 10 };
        frame.SetBorderWidthAll(1); frame.SetCornerRadiusAll(8);
        _panel.AddThemeStyleboxOverride("panel", frame);
        AddChild(_panel);

        var box = new VBoxContainer { MouseFilter = MouseFilterEnum.Ignore };
        box.AddThemeConstantOverride("separation", 6);
        _panel.AddChild(box);

        var header = new HBoxContainer { MouseFilter = MouseFilterEnum.Ignore };
        _tag = new Label { Text = "…", MouseFilter = MouseFilterEnum.Ignore };
        _tag.AddThemeFontSizeOverride("font_size", 18);
        _tag.AddThemeColorOverride("font_color", new Color("cfc7a2"));
        header.AddChild(_tag);
        _title = new Label { Text = "等待列国行动", MouseFilter = MouseFilterEnum.Ignore, SizeFlagsHorizontal = SizeFlags.ExpandFill };
        _title.AddThemeFontSizeOverride("font_size", 22);
        _title.AddThemeColorOverride("font_color", new Color("efe2b0"));
        header.AddChild(_title);
        var close = new Button { Text = "收起 ×", FocusMode = FocusModeEnum.None };
        close.Pressed += () => Visible = false;
        header.AddChild(close);
        box.AddChild(header);

        // Per-turn meta line: live context plan, measured turn time, tokens.
        _stats = new Label { Text = "🧠 上下文 — · ⏱ — · 🔢 —", MouseFilter = MouseFilterEnum.Ignore };
        _stats.AddThemeFontSizeOverride("font_size", 17);
        _stats.AddThemeColorOverride("font_color", new Color("a8b6a0"));
        box.AddChild(_stats);

        _thinkingStyle = new StyleBoxFlat { BgColor = new Color(.10f, .13f, .19f, .82f), BorderColor = new Color("4b586e"),
            ContentMarginLeft = 12, ContentMarginRight = 12, ContentMarginTop = 6, ContentMarginBottom = 6 };
        _thinkingStyle.SetBorderWidthAll(1); _thinkingStyle.SetCornerRadiusAll(6);
        _thinkingStyle.SetBorderWidthAll(1);
        _thinkingPanel = new PanelContainer { MouseFilter = MouseFilterEnum.Ignore };
        _thinkingPanel.AddThemeStyleboxOverride("panel", _thinkingStyle);
        _thinking = MakeText(new Color("9fb2c8"));
        _thinkingPanel.AddChild(_thinking);
        box.AddChild(_thinkingPanel);

        _bodyStyle = new StyleBoxFlat { BgColor = new Color(.97f, .93f, .80f, .95f), BorderColor = new Color("8a7a52"),
            ContentMarginLeft = 12, ContentMarginRight = 12, ContentMarginTop = 6, ContentMarginBottom = 6 };
        _bodyStyle.SetBorderWidthAll(1); _bodyStyle.SetCornerRadiusAll(6);
        _summaryStyle = new StyleBoxFlat { BgColor = new Color(.90f, .94f, .80f, .95f), BorderColor = new Color("7d8a54"),
            ContentMarginLeft = 12, ContentMarginRight = 12, ContentMarginTop = 6, ContentMarginBottom = 6 };
        _summaryStyle.SetBorderWidthAll(1); _summaryStyle.SetCornerRadiusAll(6);
        _bodyPanel = new PanelContainer { MouseFilter = MouseFilterEnum.Ignore };
        _bodyPanel.AddThemeStyleboxOverride("panel", _bodyStyle);
        _body = MakeText(new Color("2d2a20"));
        _bodyPanel.AddChild(_body);
        box.AddChild(_bodyPanel);

        // Portrait: model logo as the speaker standee at the panel's corner,
        // with the model name right above it (reserved logo art or a badge).
        _portrait = new Control { MouseFilter = MouseFilterEnum.Ignore, CustomMinimumSize = new Vector2(96, 112) };
        var portraitBox = new VBoxContainer { MouseFilter = MouseFilterEnum.Ignore };
        portraitBox.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        _portraitName = new Label { Text = "", HorizontalAlignment = HorizontalAlignment.Center,
            MouseFilter = MouseFilterEnum.Ignore };
        _portraitName.AddThemeFontSizeOverride("font_size", 18);
        _portraitName.AddThemeColorOverride("font_color", new Color("efe2b0"));
        portraitBox.AddChild(_portraitName);
        var logoFrame = new PanelContainer { MouseFilter = MouseFilterEnum.Ignore, CustomMinimumSize = new Vector2(84, 84) };
        var logoStyle = new StyleBoxFlat { BgColor = new Color(.10f, .13f, .12f, .96f), BorderColor = new Color("efe2b0"),
            ContentMarginLeft = 4, ContentMarginRight = 4, ContentMarginTop = 4, ContentMarginBottom = 4 };
        logoStyle.SetBorderWidthAll(2); logoStyle.SetCornerRadiusAll(6);
        logoFrame.AddThemeStyleboxOverride("panel", logoStyle);
        _logoFrame = logoFrame;
        _logoStyle = logoStyle;
        _badgeBox = new VBoxContainer { MouseFilter = MouseFilterEnum.Ignore };
        _badgeBox.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        _badge = new Label { Text = "?", HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center,
            MouseFilter = MouseFilterEnum.Ignore, SizeFlagsVertical = SizeFlags.ExpandFill };
        _badge.AddThemeFontSizeOverride("font_size", 22);
        _badge.AddThemeColorOverride("font_color", new Color("efe2b0"));
        _badgeBox.AddChild(_badge);
        logoFrame.AddChild(_badgeBox);
        portraitBox.AddChild(logoFrame);
        _portrait.AddChild(portraitBox);
        AddChild(_portrait);
        _queues["thinking"] = ("", 0);
        _queues["body"] = ("", 0);
        HideAll();
    }
    private StyleBoxFlat _logoStyle = null!;
    private static RichTextLabel MakeText(Color color)
    {
        var text = new RichTextLabel { BbcodeEnabled = false, SelectionEnabled = true, ScrollActive = true,
            ScrollFollowing = true, MouseFilter = MouseFilterEnum.Ignore, SizeFlagsVertical = SizeFlags.ExpandFill };
        text.AddThemeFontSizeOverride("normal_font_size", 20);
        text.AddThemeColorOverride("default_color", color);
        text.CustomMinimumSize = new Vector2(0, 42);
        return text;
    }

    public void SetSpeed(float speed) => _speed = Math.Max(.1f, speed);

    // Meta line: LLM turns carry all three; rule AI demo fills context from
    // frame facts and shows measured seconds with 0 tokens.
    public void SetStats(string? context, int? tokens, double? seconds)
    {
        if (context != null) _contextText = context;
        if (tokens != null) _tokenCount = tokens;
        if (seconds != null) _turnSeconds = seconds;
        RefreshStats();
    }

    private void RefreshStats()
    {
        string context = _contextText ?? "—";
        if (context.Length > 46) context = context[..46] + "…";
        string time = _turnSeconds is double seconds ? $"{seconds:0.##}s" : "—";
        string tokens = _tokenCount is int count ? count >= 10000 ? $"{count / 1000f:0.#}k" : count.ToString() : "—";
        _stats.Text = $"🧠 上下文 {context} · ⏱ {time} · 🔢 {tokens} token";
    }

    public void SetSpeaker(string actor, string nationName, string modelVersion, Color nationColor)
    {
        _actor = actor;
        _contextText = null; _tokenCount = null; _turnSeconds = null;
        RefreshStats();
        string model = ModelDisplay(modelVersion);
        _title.Text = $"{model} 扮演 {nationName}";
        _portraitName.Text = ModelShort(modelVersion);
        _logoStyle.BorderColor = nationColor;
        _badge.Text = ModelShort(modelVersion);
        var logo = LoadLogo(modelVersion);
        if (logo != null)
        {
            if (_logo == null)
            {
                logo.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
                logo.ExpandMode = TextureRect.ExpandModeEnum.IgnoreSize;
                logo.StretchMode = TextureRect.StretchModeEnum.KeepAspectCentered;
                _logoFrame.AddChild(logo);
                _logo = logo;
            }
            else _logo.Texture = GD.Load<Texture2D>($"res://assets/logos/{LogoKey(modelVersion)}.png");
        }
        else if (_logo != null)
        {
            _logoFrame.RemoveChild(_logo);
            _logo.QueueFree();
            _logo = null;
        }
        _badgeBox.Visible = _logo == null;
    }

    public void Begin(string kind)
    {
        _kind = kind;
        _queues[kind] = ("", 0);
        Target(kind).Text = "";
        _bodyPanel.AddThemeStyleboxOverride("panel", kind == "turn_end" ? _summaryStyle : _bodyStyle);
        _thinkingPanel.Visible = _queues["thinking"].Pending.Length > 0 || kind == "thinking";
        _tag.Text = kind switch { "thinking" => "💭 思考中…", "turn_end" => "🏁 回合小结", _ => "🗣 发言" };
        _holdClock = double.PositiveInfinity;
        SegmentsShown++;
        Wake();
    }

    public void Append(string kind, string text)
    {
        if(!_queues.TryGetValue(kind,out var q))q=("",0);
        _queues[kind] = (q.Pending + text, q.Shown);
        _kind = kind;
        _holdClock = double.PositiveInfinity;
    }

    // Live host stream delta: appends without clearing the current bubble.
    public void Stream(string actor, string kind, string text)
    {
        if (kind == "context") { SetStats(text, null, null); return; }
        if(!_queues.TryGetValue(kind,out var q))q=("",0);
        string separator = q.Pending.Length == 0 || q.Pending.EndsWith('\n') ? "" : "\n";
        _queues[kind] = (q.Pending + separator + text, q.Shown);
        _kind = kind;
        _thinkingPanel.Visible = _queues["thinking"].Pending.Length > 0;
        _holdClock = double.PositiveInfinity;
        SegmentsShown++;
    }

    public void Park()
    {
        Modulate = new Color(1, 1, 1, .45f);
        _tag.Text = "— 演出中 —";
    }
    public void Wake()
    {
        Modulate = Colors.White;
        _tag.Text = _kind == "thinking" ? "💭 思考中…" : _kind == "turn_end" ? "🏁 回合小结" : "🗣 发言";
    }
    public void HideSoon() => _holdClock = 1.6;
    public void HideAll()
    {
        _panel.Visible = false; _portrait.Visible = false;
    }
    private void ShowPanels()
    {
        _panel.Visible = true; _portrait.Visible = true; EverShown = true; Wake();
    }

    private RichTextLabel Target(string kind) => kind == "thinking" ? _thinking : _body;

    public override void _Process(double delta)
    {
        // The standee tracks the dialogue frame even while everything is hidden.
        _portrait.Position = _panel.GlobalPosition - GlobalPosition + new Vector2(_panel.Size.X - 44, _panel.Size.Y - 78);
        // The reveal loop must run while hidden too: it is what brings the
        // panel back after HideAll(). Only the outer toggle can stop it.
        if (!IsVisibleInTree()) return;
        bool streaming = false;
        foreach (var kind in _queues.Keys)
        {
            var q = _queues[kind];
            if (q.Shown >= q.Pending.Length) continue;
            // Only the active bubble and the thinking log advance their reveal.
            if (kind != _kind && kind != "thinking") continue;
            float rate = (kind == "thinking" ? 260 : 90) * _speed;
            int next = Math.Min(q.Pending.Length, q.Shown + Math.Max(1, (int)(rate * delta)));
            Target(kind).Text = q.Pending[..next] + (next == q.Pending.Length ? "" : "▌");
            _queues[kind] = (q.Pending, next);
            streaming = true;
        }
        _thinkingPanel.Visible = _queues["thinking"].Pending.Length > 0;
        if (streaming) { ShowPanels(); _tag.Text = _kind == "thinking" ? "💭 思考中…" : "🗣 发言"; }
        else if (_holdClock != double.PositiveInfinity)
        {
            _holdClock -= delta;
            if (_holdClock <= 0) { HideAll(); _holdClock = double.PositiveInfinity; }
        }
    }

    private static string ModelDisplay(string version) => version switch
    {
        "" => "未知模型",
        var v when IsRuleVersion(v) => $"规则AI {v}",
        _ => version
    };
    // v13-style short tags mark rule clients; they get the preset demo stream.
    public static bool IsRuleVersion(string version) =>
        version.Length is >= 2 and <= 4 && version[0] == 'v' && version[1..].All(char.IsDigit);
    internal static string ModelShort(string version)
    {
        foreach (var (keyword, name) in ModelKeywords)
            if (version.Contains(keyword)) return name;
        var v = version.Trim();
        return v.Length <= 6 ? (v == "" ? "AI" : v) : v[..6];
    }
    private static readonly (string Keyword, string Name)[] ModelKeywords =
    {
        ("deepseek", "DeepSeek"), ("glm", "GLM"), ("zhipu", "GLM"), ("gemini", "Gemini"),
        ("openai", "GPT"), ("gpt", "GPT"), ("o1", "GPT"), ("o3", "GPT"), ("o4", "GPT"),
        ("qwen", "Qwen"), ("qwq", "Qwen"), ("kimi", "Kimi"), ("moonshot", "Kimi"),
        ("claude", "Claude"), ("grok", "Grok"), ("llama", "Llama"), ("hunyuan", "混元"),
        ("doubao", "豆包"), ("minimax", "MiniMax"), ("abab", "MiniMax"), ("rule", "规则AI"),
    };
    internal static string LogoKey(string version)
    {
        foreach (var (keyword, name) in ModelKeywords)
            if (version.Contains(keyword)) return name.ToLowerInvariant();
        return "generic";
    }
    private static TextureRect? LoadLogo(string version)
    {
        string path = $"res://assets/logos/{LogoKey(version)}.png";
        if (!ResourceLoader.Exists(path)) return null;
        return new TextureRect { Texture = GD.Load<Texture2D>(path) };
    }
}
