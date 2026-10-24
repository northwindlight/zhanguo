using Godot;
using System;
using System.Linq;
using System.Text.Json.Nodes;

// Only model-authored text enters this overlay. Action receipts belong to the
// playback banner. Provider deltas are already streamed; never type them twice.
public partial class SpeechBubbles : Control
{
    private PanelContainer _panel = null!, _portraitFrame = null!, _thinkingPanel = null!;
    private VBoxContainer _column = null!;
    private Label _tag = null!, _title = null!, _stats = null!, _portraitName = null!, _badge = null!;
    private RichTextLabel _thinking = null!, _body = null!, _summary = null!;
    private Button _fold = null!, _restore = null!;
    private TextureRect _portrait = null!;
    private StyleBoxFlat _portraitStyle = null!;
    private string _actor = "", _model = "", _context = "";
    private string _thought = "", _prose = "";
    private bool _expanded, _collapsed, _hasText;
    private double _hideAfter = double.PositiveInfinity;
    public int SegmentsShown { get; private set; }
    public bool EverShown { get; private set; }
    public bool Streaming { get; private set; }
    public Rect2 DialogueBounds => _panel.GetGlobalRect();
    public Rect2 PortraitBounds => _portraitFrame.GetGlobalRect();
    public bool HasPortrait => _portrait.Texture != null;
    public Texture2D? PortraitTexture => _portrait.Texture;
    public string BodyText => _body.Text;
    public string ThoughtText => _thinking.Text;
    public string Speaker => _actor;

    public override void _Ready()
    {
        MouseFilter = MouseFilterEnum.Ignore;
        _panel = new PanelContainer { MouseFilter = MouseFilterEnum.Stop };
        _panel.AddThemeStyleboxOverride("panel", Frame(new Color("132222"), new Color("647369"), 16));
        AddChild(_panel);
        _column = new VBoxContainer(); _column.AddThemeConstantOverride("separation", 8); _panel.AddChild(_column);
        var header = new HBoxContainer(); _column.AddChild(header);
        _tag = Label("模型发言", 14, "a6b9ae"); header.AddChild(_tag);
        _title = Label("", 20, "f0e0b4");
        _title.SizeFlagsHorizontal = SizeFlags.ExpandFill;
        _title.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
        header.AddChild(_title);
        var close = new Button { Text = "收起", FocusMode = FocusModeEnum.None };
        close.AddThemeFontSizeOverride("font_size", 14);
        close.Pressed += () => { _collapsed = true; Refresh(); };
        header.AddChild(close);
        _stats = Label("", 14, "a6b9ae");
        _stats.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
        _column.AddChild(_stats);
        _fold = new Button { Text = "思考过程 ▸", Alignment = HorizontalAlignment.Left, FocusMode = FocusModeEnum.None };
        _fold.AddThemeFontSizeOverride("font_size", 14);
        _fold.Pressed += () => { _expanded = !_expanded; Refresh(); };
        _column.AddChild(_fold);
        _thinkingPanel = new PanelContainer();
        _thinkingPanel.AddThemeStyleboxOverride("panel", Frame(new Color("1b2932"), new Color("374951"), 10));
        _thinking = Text("b4c7d3", 16); _thinkingPanel.AddChild(_thinking); _column.AddChild(_thinkingPanel);
        _body = Text("efe5ca", 19); _column.AddChild(_body);
        _summary = Text("b8ceac", 16); _column.AddChild(_summary);

        _portraitFrame = new PanelContainer { MouseFilter = MouseFilterEnum.Ignore };
        _portraitStyle = Frame(new Color(.055f, .09f, .10f, .9f), new Color("8b9b82"), 8);
        _portraitFrame.AddThemeStyleboxOverride("panel", _portraitStyle); AddChild(_portraitFrame);
        var portraitBox = new VBoxContainer(); _portraitFrame.AddChild(portraitBox);
        _portraitName = Label("", 15, "efe5ca");
        _portraitName.HorizontalAlignment = HorizontalAlignment.Center;
        _portraitName.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
        portraitBox.AddChild(_portraitName);
        _portrait = new TextureRect { Name="PortraitArt", ExpandMode = TextureRect.ExpandModeEnum.IgnoreSize,
            StretchMode = TextureRect.StretchModeEnum.KeepAspectCentered,
            SizeFlagsVertical = SizeFlags.ExpandFill, TextureFilter = TextureFilterEnum.Linear };
        portraitBox.AddChild(_portrait);
        _badge = Label("AI", 24, "d9c99b"); _badge.HorizontalAlignment = HorizontalAlignment.Center;
        _badge.VerticalAlignment = VerticalAlignment.Center; _badge.SizeFlagsVertical = SizeFlags.ExpandFill;
        portraitBox.AddChild(_badge);
        _fold.Name="ThoughtToggle";
        close.Name="CollapseDialogue";
        _restore = new Button { Name="RestoreDialogue",Text = "模型发言 ▴", FocusMode = FocusModeEnum.None };
        _restore.Pressed += () => { _collapsed = false; Refresh(); }; AddChild(_restore);
        Resized += Layout; HideAll();
    }
    private static Label Label(string text, int size, string color)
    {
        var label = new Label { Text = text, MouseFilter = MouseFilterEnum.Ignore };
        label.AddThemeFontSizeOverride("font_size", size); label.AddThemeColorOverride("font_color", new Color(color)); return label;
    }
    private static RichTextLabel Text(string color, int size)
    {
        var text = new RichTextLabel { BbcodeEnabled = false, SelectionEnabled = true,
            ScrollActive = true, ScrollFollowing = true, MouseFilter = MouseFilterEnum.Stop,
            AutowrapMode = TextServer.AutowrapMode.WordSmart };
        text.AddThemeFontSizeOverride("normal_font_size", size);
        text.AddThemeColorOverride("default_color", new Color(color));
        text.AddThemeConstantOverride("line_separation", 4); return text;
    }
    private static StyleBoxFlat Frame(Color background, Color border, int margin)
    {
        var style = new StyleBoxFlat { BgColor = background, BorderColor = border,
            ContentMarginLeft = margin, ContentMarginRight = margin, ContentMarginTop = 10, ContentMarginBottom = 10 };
        style.SetBorderWidthAll(1); style.SetCornerRadiusAll(0); return style;
    }
    public void SetSpeaker(string actor, string nationName, string model, Color color)
    {
        if (_actor == actor && _model == model) return;
        _actor = actor; _model = model; _context = "";
        _thought = _prose = ""; _thinking.Text = _body.Text = _summary.Text = _stats.Text = "";
        _title.Text = $"{nationName} · {(IsRuleVersion(model) ? "规则 AI " + model : model == "" ? "模型" : model)}";
        _title.TooltipText = _title.Text;
        _portraitName.Text = ModelShort(model); _portraitStyle.BorderColor = color;
        _badge.Text = ModelShort(model);
        SetPortrait(LoadPortrait(model));
        _hasText = false; Streaming = false; Refresh();
    }
    public void SetPortrait(Texture2D? texture)
    {
        _portrait.Texture=texture;_portrait.Visible=texture!=null;_badge.Visible=texture==null;
    }
    public void ShowRuleTurn()
    {
        Streaming = false; _thought = _prose = ""; _thinking.Text = ""; _summary.Text = ""; _stats.Text = "";
        _tag.Text = "规则对局";
        _body.Text = "本国的建设、交易和军令正在上方横幅逐项展示。";
        _hasText = true; SegmentsShown++; _hideAfter = double.PositiveInfinity; Refresh();
    }
    public void HandleStream(JsonObject row)
    {
        string Value(string key) => row[key]?.ToString() ?? "";
        string op = Value("event"), kind = Value("kind");
        _hideAfter = double.PositiveInfinity;
        switch (op)
        {
            case "context":
                _context = Value("text"); _stats.TooltipText = _context; return;
            case "begin":
                _thought = _prose = ""; _thinking.Text = ""; _summary.Text = "";
                _body.Text = "等待模型响应…"; _tag.Text = "等待响应"; _stats.Text = $"第 {Value("call")} 次调用";
                Streaming = true; _hasText = true; SegmentsShown++; break;
            case "reset":
                _thought = _prose = ""; _thinking.Text = ""; _body.Text = "等待模型响应…"; break;
            case "delta":
                if (kind == "thinking") { _thought = Bounded(_thought + Value("text")); _thinking.Text = _thought; if(_prose.Length==0)_body.Text="正在生成发言…"; }
                if (kind == "body") { _prose = Bounded(_prose + Value("text")); _body.Text = _prose; }
                _tag.Text = kind == "thinking" ? "思考中" : "正在发言";
                _hasText = true; Streaming = true; break;
            case "complete":
                _thought = Bounded(Value("thinking")); _prose = Bounded(Value("body"));
                _thinking.Text = _thought; _body.Text = _prose.Length > 0 ? _prose : "本次响应未附发言正文。";
                _tag.Text = "模型发言"; Streaming = false;
                string tokens = row["tokens"] == null ? "用量未报告" : $"{(row["estimated"]?.GetValue<bool>() == true ? "约 " : "")}{Value("tokens")} 输出 token";
                _stats.Text = tokens + (row["seconds"] == null ? "" : $" · {row["seconds"]!.GetValue<double>():0.0} 秒"); break;
            case "summary":
                _summary.Text = "回合小结  " + Bounded(Value("text")); _tag.Text = "行动结束"; Streaming = false; break;
            case "failed":
                _tag.Text = "响应中断 · 未完成"; Streaming = false; break;
            case "message":
                if (kind == "thinking") { _thought = Bounded(Value("text")); _thinking.Text = _thought; }
                else { _prose = Bounded(Value("text")); _body.Text = _prose; }
                _hasText = true; SegmentsShown++; break;
            default: return;
        }
        Refresh();
    }
    private static string Bounded(string text) => text.Length <= 40000 ? text : "…（前文见纪事）\n" + text[^39000..];
    public void SetSpeed(float speed) { } // Actual provider arrival controls streaming.
    public void Park() { }
    public void HideSoon() => _hideAfter = 8;
    public void HideAll() { _hasText = false; _panel.Visible = _portraitFrame.Visible = _restore.Visible = false; }
    private void Refresh()
    {
        _fold.Visible = _thought.Length > 0;
        _fold.Text = _expanded ? "思考过程 ▾  收起详情" : "思考过程 ▸  展开详情";
        _thinkingPanel.Visible = _thought.Length > 0;
        _summary.Visible = _summary.Text.Length > 0;
        _stats.Visible = _stats.Text.Length > 0;
        _panel.Visible = _portraitFrame.Visible = _hasText && !_collapsed;
        _restore.Visible = _hasText && _collapsed;
        if (_panel.Visible) EverShown = true;
        Layout();
    }
    private void Layout()
    {
        if (_panel == null || Size.X < 100) return;
        float width = Math.Min(960, Size.X - 32), portraitW = Size.X < 1100 ? 160 : 216;
        float available = Math.Max(160, Size.Y - 300);
        bool compact=Size.Y<800;
        float bodyH = _prose.Length > 0 || Streaming ? (compact?88:108) : 48;
        float thinkingH = _thought.Length > 0 ? (_expanded ? (compact?72:146) : (compact?32:48)) : 0;
        _body.CustomMinimumSize = new Vector2(0, bodyH);
        _thinking.CustomMinimumSize = new Vector2(0, thinkingH);
        _summary.CustomMinimumSize = new Vector2(0, _summary.Visible ? (compact?32:54) : 0);
        float height = 42 + bodyH + (thinkingH > 0 ? thinkingH + 62 : 0) + (_stats.Visible ? 26 : 0) + (_summary.Visible ? 62 : 0);
        if (height > available)
        {
            _thinking.CustomMinimumSize = new Vector2(0, Math.Max(32, thinkingH - (height - available)));
            height = available;
        }
        height=Math.Max(height,_panel.GetCombinedMinimumSize().Y);
        float bottom = Size.Y - 78;
        _panel.Position = new Vector2(Size.X - width - 16, bottom - height);
        _panel.Size = new Vector2(width - portraitW - 12, height);
        // Minimum-size changes propagate through containers on the next frame.
        float portraitH=HasPortrait?260:Math.Min(260,height);
        _portraitFrame.Position = new Vector2(Size.X - portraitW - 16, bottom - portraitH);
        _portraitFrame.Size = new Vector2(portraitW, portraitH);
        _restore.Position = new Vector2(Size.X - 170, bottom - 42); _restore.Size = new Vector2(154, 38);
    }
    public override void _Process(double delta)
    {
        Layout();
        if (!Streaming && !double.IsPositiveInfinity(_hideAfter))
        {
            _hideAfter -= delta; if (_hideAfter <= 0) HideAll();
        }
    }
    public static bool IsRuleVersion(string version) => version.StartsWith("v", StringComparison.OrdinalIgnoreCase) && version[1..].All(char.IsDigit);
    private static readonly (string Key, string Name)[] Models = {
        ("deepseek", "DeepSeek"), ("qwen", "Qwen"), ("qwq", "Qwen"), ("glm", "GLM"), ("zhipu", "GLM"),
        ("gemini", "Gemini"), ("gpt", "GPT"), ("openai", "GPT"), ("kimi", "Kimi"), ("moonshot", "Kimi"),
        ("claude", "Claude"), ("grok", "Grok"), ("llama", "Llama"), ("hunyuan", "Hunyuan"),
        ("doubao", "Doubao"), ("minimax", "MiniMax"), ("abab", "MiniMax") };
    internal static string ModelShort(string version)
    {
        string lower = version.ToLowerInvariant();
        foreach (var (key, name) in Models) if (lower.Contains(key)) return name;
        return IsRuleVersion(version) ? "规则 AI" : version.Length > 12 ? version[..12] + "…" : version.Length == 0 ? "AI" : version;
    }
    internal static string LogoKey(string version)
    {
        string lower = version.ToLowerInvariant();
        foreach (var (key, name) in Models) if (lower.Contains(key)) return name.ToLowerInvariant();
        return "generic";
    }
    private static readonly System.Collections.Generic.Dictionary<string,Texture2D> PortraitCache=new();
    private static JsonObject? _portraitManifest;
    private static Texture2D? LoadPortrait(string version)
    {
        string key = LogoKey(version);
        foreach (var path in new[] { $"res://assets/portraits/{key}.png", $"res://assets/logos/{key}.png",
                     "res://assets/portraits/generic.png", "res://assets/logos/generic.png" }.Distinct())
        {
            if(!ResourceLoader.Exists(path))continue;
            if(PortraitCache.TryGetValue(path,out var cached))return cached;
            Texture2D texture=GD.Load<Texture2D>(path);
            if(path.StartsWith("res://assets/portraits/",StringComparison.Ordinal))
            {
                _portraitManifest??=ReadPortraitManifest();
                string name=System.IO.Path.GetFileNameWithoutExtension(path);
                if(_portraitManifest["portraits"]?[name]?["display_region"] is JsonArray region&&region.Count==4)
                {
                    float x=region[0]!.GetValue<float>(),y=region[1]!.GetValue<float>();
                    float w=region[2]!.GetValue<float>(),h=region[3]!.GetValue<float>();
                    if(x>=0&&y>=0&&w>0&&h>0&&x+w<=texture.GetWidth()&&y+h<=texture.GetHeight())
                        texture=new AtlasTexture { Atlas=texture,Region=new Rect2(x,y,w,h),FilterClip=true };
                }
            }
            PortraitCache[path]=texture;return texture;
        }
        return null;
    }
    private static JsonObject ReadPortraitManifest()
    {
        const string path="res://assets/portraits/manifest.json";
        try
        {
            if(Godot.FileAccess.FileExists(path))return JsonNode.Parse(Godot.FileAccess.GetFileAsString(path))?.AsObject()??new JsonObject();
        }
        catch(Exception e){GD.PrintErr("半身图清单读取失败，显示原图："+e.Message);}
        return new JsonObject();
    }
}
