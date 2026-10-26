using Godot;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;

// Start screen with the same expressive power as mp_config.json: scenario
// subset / nation count, seed, map size, per-nation client (rule/sim/llm with
// a persisted API profile), plus free-form JSON extras merged on top for any
// field the forms do not enumerate. Pure presentation; the host stays the
// only interpreter of the produced config.
public partial class MainMenu : Control
{
    public sealed class ApiProfile
    {
        public string Name = "新档案", Provider = "openai", BaseUrl = "", ApiKey = "", Model = "", ExtraJson = "";
        public JsonObject ToJson() => new()
        {
            ["name"] = Name, ["provider"] = Provider, ["base_url"] = BaseUrl,
            ["api_key"] = ApiKey, ["model"] = Model, ["extra"] = ExtraJson,
        };
        public static ApiProfile FromJson(JsonObject o) => new()
        {
            Name = o["name"]?.ToString() ?? "档案", Provider = o["provider"]?.ToString() ?? "openai",
            BaseUrl = o["base_url"]?.ToString() ?? "", ApiKey = o["api_key"]?.ToString() ?? "",
            Model = o["model"]?.ToString() ?? "", ExtraJson = o["extra"]?.ToString() ?? "",
        };
    }
    private sealed class NationDraft
    {
        public int Preset; public string Name = "";
        public int Client;                      // 0 rule, 1 sim, 2 llm
        public string RuleAi = "v13"; public int Strategy; public string RuleSeed = "";
        public int Profile; public string Model = "", Thinking = "enabled", Effort = "low";
        public string Temperature = "0.3", MaxTokens = "16384", MaxSteps = "28", ExtraJson = "";
    }
    private static readonly (string Id, string Name, string Extra)[] Presets =
    {
        ("scenario:qin", "秦", ""), ("scenario:wei", "魏", ""), ("scenario:han", "韩", ""),
        ("scenario:zhao", "赵", ""), ("scenario:yan", "燕", ""), ("scenario:qi", "齐", ""),
        ("scenario:chu", "楚", ""), ("scenario:zhou", "周", ""),
        ("scenario:linhu", "林胡", "{\"polity\":\"huns\"}"),
        ("scenario:loufan", "楼烦", "{\"polity\":\"huns\",\"appearance\":\"manual\"}"),
    };
    private static readonly string[] Strategies = { "random", "economy", "military", "defense" };
    private static readonly string[] Clients = { "规则AI", "简易策略", "大模型" };

    public Action<string?, string?>? StartRequested;   // (config path, resume session path)
    private readonly List<ApiProfile> _profiles = new();
    private readonly List<NationDraft> _nations = new();
    private readonly List<(Control Row, NationDraft Draft)> _rows = new();
    private readonly List<(OptionButton Box,NationDraft Draft)> _profilePickers = new();
    private Label _hint = null!;
    private Control _home = null!, _newGame = null!, _load = null!, _settings = null!;
    private LineEdit _seed = null!, _globalExtra = null!;
    private SpinBox _mapSize = null!, _maxTurns = null!, _ctxWindow = null!;
    private VBoxContainer _nationList = null!;
    private VBoxContainer _profileList = null!;
    private readonly string _settingsPath;
    private readonly JsonArray _scenarioCatalog=JsonNode.Parse(Godot.FileAccess.GetFileAsString("res://scenario-catalog.json"))!["scenarios"]!.AsArray();
    private OptionButton _scenarioPicker=null!;
    private Label _scenarioInfo=null!;
    private Button _addNationButton=null!;
    private JsonObject SelectedScenario=>_scenarioCatalog[_scenarioPicker.Selected]!.AsObject();
    private bool FixedScenario=>SelectedScenario["fixed_roster"]!.GetValue<bool>();

    public LineEdit SeedField => _seed;

    public MainMenu(string root, Action<string?, string?> start)
    {
        _settingsPath = Path.Combine(root, "archive", "desktop-settings.json");
        StartRequested = start;
    }

    public override void _Ready()
    {
        SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        var back = new ColorRect { Color = new Color(.043f, .075f, .08f) };
        back.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect); AddChild(back);
        var center = new Control();
        center.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        center.OffsetLeft = 32; center.OffsetRight = -32; center.OffsetTop = 24; center.OffsetBottom = -24; AddChild(center);
        var title = new Label { Text = "战 国", HorizontalAlignment = HorizontalAlignment.Center };
        title.SetAnchorsAndOffsetsPreset(LayoutPreset.TopWide); title.OffsetBottom=58;
        title.AddThemeFontSizeOverride("font_size", 42);
        title.AddThemeColorOverride("font_color", new Color("e8d9a8"));
        center.AddChild(title);
        var subtitle = new Label { Text = "观天下 · 上帝视角", HorizontalAlignment = HorizontalAlignment.Center,
            Modulate = new Color("a4b09c") };
        subtitle.SetAnchorsAndOffsetsPreset(LayoutPreset.TopWide); subtitle.OffsetTop=60; subtitle.OffsetBottom=88;
        subtitle.AddThemeFontSizeOverride("font_size", 18); center.AddChild(subtitle);
        string[] palette = { "9eb5cf", "e5a47c", "c69ddd", "91bd98", "e8cc83", "76bdbb", "d68c95", "ddd9af" };
        var strip = new HBoxContainer(); strip.SetAnchorsAndOffsetsPreset(LayoutPreset.TopWide); strip.OffsetTop=98; strip.OffsetBottom=104; strip.AddThemeConstantOverride("separation", 4);
        for (int i = 0; i < 8; i++)
        {
            var cell = new ColorRect { Color = new Color(palette[i]), CustomMinimumSize = new Vector2(64, 6), SizeFlagsHorizontal = SizeFlags.ExpandFill };
            strip.AddChild(cell);
        }
        center.AddChild(strip);
        _hint = new Label { Text = "", HorizontalAlignment = HorizontalAlignment.Center, Modulate = new Color("e0a980") };
        _hint.SetAnchorsAndOffsetsPreset(LayoutPreset.BottomWide); _hint.OffsetTop=-36; _hint.OffsetBottom=0;
        _hint.AutowrapMode=TextServer.AutowrapMode.WordSmart; _hint.AddThemeFontSizeOverride("font_size", 16); center.AddChild(_hint);

        _home = Section(center);
        _home.SetAnchorsAndOffsetsPreset(LayoutPreset.Center);
        _home.OffsetLeft=-220; _home.OffsetRight=220; _home.OffsetTop=-76; _home.OffsetBottom=180;
        BigButton(_home, "新的对局", () => OpenPanel(_newGame, () => { if (_nations.Count == 0) DefaultEightNations(); }));
        BigButton(_home, "加载存档", () => { OpenPanel(_load, RefreshSaves); });
        BigButton(_home, "设置 · API 档案", () => { OpenPanel(_settings, RefreshProfiles); });
        BigButton(_home, "退出", () => GetTree().Quit());
        _newGame = BuildNewGame(center);
        _load = BuildLoad(center);
        _settings = BuildSettings(center);
        OpenPanel(_home);
        LoadSettings();
    }

    private static Control Section(Control parent)
    {
        var box = new VBoxContainer { Visible = false };
        box.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        box.OffsetLeft = 8; box.OffsetRight = -8; box.OffsetTop = 124; box.OffsetBottom = -46;
        box.AddThemeConstantOverride("separation", 10);
        parent.AddChild(box); return box;
    }
    private static Button BigButton(Control parent, string text, Action pressed)
    {
        var button = new Button { Text = text, CustomMinimumSize = new Vector2(0, 46), FocusMode = FocusModeEnum.None };
        button.AddThemeFontSizeOverride("font_size", 20);
        button.Pressed += pressed; parent.AddChild(button); return button;
    }
    private void OpenPanel(Control panel, Action? after = null)
    {
        foreach (var section in new[] { _home, _newGame, _load, _settings }) section.Visible = section == panel;
        _hint.Text = "";
        if(panel==_newGame)foreach(var (box,draft) in _profilePickers)RefreshProfileOptions(box,draft);
        after?.Invoke();
    }
    public void OpenNewGame() => OpenPanel(_newGame, () => { if (_nations.Count == 0) DefaultEightNations(); });
    public void OpenLoad() { OpenPanel(_load, RefreshSaves); }
    public void OpenSettings() { OpenPanel(_settings, RefreshProfiles); }

    // ---------------------------------------------------------------- new game
    private Control BuildNewGame(Control parent)
    {
        var panel = Section(parent);
        var scroll = new ScrollContainer { SizeFlagsVertical = SizeFlags.ExpandFill, HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled };
        panel.AddChild(scroll);
        var box = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        box.AddThemeConstantOverride("separation", 8); scroll.AddChild(box);
        var scenarioRow=new HBoxContainer();scenarioRow.AddChild(FieldLabel("开局剧本"));
        _scenarioPicker=new OptionButton { SizeFlagsHorizontal=SizeFlags.ExpandFill };
        foreach(var scenario in _scenarioCatalog)_scenarioPicker.AddItem(scenario!["name"]!.ToString());
        scenarioRow.AddChild(_scenarioPicker);box.AddChild(scenarioRow);
        _scenarioInfo=new Label { AutowrapMode=TextServer.AutowrapMode.WordSmart,Modulate=new Color("b7c5ad") };box.AddChild(_scenarioInfo);
        _scenarioPicker.ItemSelected+=_=>ChangeScenario();
        var basics = new HFlowContainer(); basics.AddThemeConstantOverride("h_separation", 12); basics.AddThemeConstantOverride("v_separation", 8);
        HBoxContainer Setting(string name)
        {
            var group=new HBoxContainer();group.AddChild(FieldLabel(name));basics.AddChild(group);return group;
        }
        var seedGroup=Setting("种子");
        _seed = new LineEdit { Text="20261023",CustomMinimumSize=new Vector2(134,0) }; seedGroup.AddChild(_seed);
        var random=new Button { Text="随机",FocusMode=FocusModeEnum.None }; seedGroup.AddChild(random);
        random.Pressed+=()=>_seed.Text=Random.Shared.Next(1,2_000_000_000).ToString();
        _mapSize=new SpinBox { MinValue=12,MaxValue=96,Value=24,Step=1,CustomMinimumSize=new Vector2(100,0) };
        Setting("地图边长").AddChild(_mapSize);
        _maxTurns=new SpinBox { MinValue=10,MaxValue=3000,Value=300,Step=10,CustomMinimumSize=new Vector2(116,0) };
        Setting("回合上限").AddChild(_maxTurns);
        _ctxWindow=new SpinBox { MinValue=8000,MaxValue=4_000_000,Value=128000,Step=8000,CustomMinimumSize=new Vector2(140,0) };
        Setting("上下文窗口").AddChild(_ctxWindow);
        box.AddChild(basics);
        var header = new HBoxContainer();
        header.AddChild(FieldLabel("参战国家（2–10，行序即行动顺序）"));
        header.AddChild(new Control { SizeFlagsHorizontal = SizeFlags.ExpandFill });
        var add = new Button { Text = "+ 添加国家", FocusMode = FocusModeEnum.None };
        _addNationButton=add;
        add.Pressed += () => { if (_nations.Count < 10) AddNationRow(new NationDraft { Preset = FirstFreePreset(), Name = Presets[FirstFreePreset()].Name }); };
        header.AddChild(add); box.AddChild(header);

        _nationList = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        _nationList.AddThemeConstantOverride("separation", 12); box.AddChild(_nationList);
        var extraLabel = FieldLabel("全局附加 JSON（合并进配置顶层，可写 ctx_fill/rule_ai/mods 等任意字段）");
        extraLabel.AutowrapMode=TextServer.AutowrapMode.WordSmart;
        box.AddChild(extraLabel);
        _globalExtra = new LineEdit { PlaceholderText = "{ }" }; box.AddChild(_globalExtra);
        var start = new Button { Text = "开始对局", CustomMinimumSize = new Vector2(0, 44), FocusMode = FocusModeEnum.None };
        start.AddThemeFontSizeOverride("font_size", 22);
        start.Pressed += () =>
        {
            var config = BuildConfig(out var error, out var path);
            if (config == null) { _hint.Text = error; return; }
            StartRequested?.Invoke(path, null);
        };
        var footer=new HBoxContainer();start.SizeFlagsHorizontal=SizeFlags.ExpandFill;
        footer.AddChild(start);footer.AddChild(BackButton());panel.AddChild(footer);
        return panel;
    }

    private int FirstFreePreset()
    {
        var used = _nations.Select(n => n.Preset).ToHashSet();
        for (int i = 0; i < Presets.Length; i++) if (!used.Contains(i)) return i;
        return 0;
    }
    private void DefaultEightNations()
    {
        ChangeScenario();
    }
    public void SelectScenario(int index)
    {
        _scenarioPicker.Selected=index;ChangeScenario();
    }
    private void ChangeScenario()
    {
        foreach(Node child in _nationList.GetChildren()){_nationList.RemoveChild(child);child.QueueFree();}
        _nations.Clear();_rows.Clear();_profilePickers.Clear();
        _scenarioInfo.Text=SelectedScenario["description"]!.ToString();
        _mapSize.Value=SelectedScenario["map_size"]!.GetValue<int>();_mapSize.Editable=!FixedScenario;
        _addNationButton.Disabled=FixedScenario;
        foreach(var nation in SelectedScenario["nations"]!.AsArray())
        {
            int preset=Array.FindIndex(Presets,p=>p.Id==nation!["id"]!.ToString());
            if(preset<0)throw new InvalidDataException("剧本国家缺少玩家预设");
            AddNationRow(new NationDraft { Preset=preset,Name=nation!["name"]!.ToString() });
        }
    }
    private void AddNationRow(NationDraft draft)
    {
        _nations.Add(draft);
        var card=new PanelContainer();
        var cardStyle=new StyleBoxFlat { BgColor=new Color("1b2b2c"),ContentMarginLeft=12,ContentMarginRight=12,ContentMarginTop=10,ContentMarginBottom=10 };
        cardStyle.SetCornerRadiusAll(0);card.AddThemeStyleboxOverride("panel",cardStyle);
        var stack=new VBoxContainer();stack.AddThemeConstantOverride("separation",8);card.AddChild(stack);
        var row = new HBoxContainer(); stack.AddChild(row);
        row.AddThemeConstantOverride("separation", 8);
        var preset = new OptionButton { FocusMode = FocusModeEnum.None };
        preset.Disabled=FixedScenario;
        foreach (var p in Presets) preset.AddItem(p.Name);
        preset.Selected = draft.Preset;
        row.AddChild(preset);
        var name = new LineEdit { Text = draft.Name, CustomMinimumSize = new Vector2(90, 0) };
        preset.ItemSelected += index => { draft.Preset=(int)index; name.Text=Presets[draft.Preset].Name; };
        name.TextChanged += value => draft.Name = value;
        row.AddChild(name);
        var client = new OptionButton { Name="NationClient",FocusMode = FocusModeEnum.None };
        foreach (var c in Clients) client.AddItem(c);
        client.Selected = draft.Client;
        var rule = new HBoxContainer();
        var llm = new HBoxContainer();
        BuildRuleFields(rule, draft);
        BuildLlmFields(llm, draft);
        client.ItemSelected += index =>
        {
            draft.Client = (int)index;
            rule.Visible = draft.Client == 0; llm.Visible = draft.Client == 2;
        };
        rule.Visible = draft.Client == 0; llm.Visible = draft.Client == 2;
        row.AddChild(client);
        row.AddChild(new Control { SizeFlagsHorizontal=SizeFlags.ExpandFill });
        stack.AddChild(rule); stack.AddChild(llm);
        var extra = new LineEdit { Text = draft.ExtraJson, PlaceholderText = "附加JSON", CustomMinimumSize = new Vector2(150, 0) };
        extra.TextChanged += value => draft.ExtraJson = value;
        stack.AddChild(extra);
        var remove = new Button { Text = "−", FocusMode = FocusModeEnum.None };
        remove.Disabled=FixedScenario;
        remove.Pressed += () => { if (_nations.Count > 2) { _nations.Remove(draft); _rows.RemoveAll(r => r.Draft == draft); _profilePickers.RemoveAll(p=>p.Draft==draft);card.QueueFree(); } };
        row.AddChild(remove);
        _nationList.AddChild(card);
        _rows.Add((card, draft));
    }
    private void BuildRuleFields(HBoxContainer rule, NationDraft draft)
    {
        rule.AddChild(FieldLabel("版本"));
        var ai = new LineEdit { Text = draft.RuleAi, CustomMinimumSize = new Vector2(64, 0) };
        ai.TextChanged += v => draft.RuleAi = v; rule.AddChild(ai);
        rule.AddChild(FieldLabel("风格"));
        var strategy = new OptionButton { FocusMode = FocusModeEnum.None };
        foreach (var s in Strategies) strategy.AddItem(s);
        strategy.Selected = draft.Strategy;
        strategy.ItemSelected += i => draft.Strategy = (int)i; rule.AddChild(strategy);
        rule.AddChild(FieldLabel("种子"));
        var seed = new LineEdit { Text = draft.RuleSeed, CustomMinimumSize = new Vector2(80, 0) };
        seed.TextChanged += v => draft.RuleSeed = v; rule.AddChild(seed);
    }
    private void BuildLlmFields(HBoxContainer llm, NationDraft draft)
    {
        llm.AddChild(FieldLabel("档案"));
        var profile = new OptionButton { FocusMode = FocusModeEnum.None };
        RefreshProfileOptions(profile, draft);
        profile.ItemSelected += i => draft.Profile = (int)i;
        _profilePickers.Add((profile,draft));
        profile.SizeFlagsHorizontal=SizeFlags.ExpandFill;
        profile.CustomMinimumSize=new Vector2(160,0);profile.ClipText=true;
        llm.AddChild(profile);
        llm.AddChild(FieldLabel("模型"));
        var model = new LineEdit { Text = draft.Model, CustomMinimumSize = new Vector2(150, 0) };
        model.TextChanged += v => draft.Model = v; llm.AddChild(model);
        var effort = new OptionButton { FocusMode = FocusModeEnum.None };
        foreach (var e in new[] { "low", "medium", "high", "max" }) effort.AddItem(e);
        effort.Selected = 0; effort.ItemSelected += i => draft.Effort = ((int)i) switch { 1 => "medium", 2 => "high", 3 => "max", _ => "low" };
        llm.AddChild(effort);
    }
    private void RefreshProfileOptions(OptionButton box, NationDraft draft)
    {
        box.Clear();
        if (_profiles.Count == 0) box.AddItem("（先在设置中添加 API 档案）");
        else foreach (var p in _profiles) box.AddItem(p.Name);
        draft.Profile = Math.Clamp(draft.Profile, 0, Math.Max(0, _profiles.Count - 1));
        box.Selected = _profiles.Count == 0 ? 0 : draft.Profile;
    }

    /// 写出等价 mp_config；返回 null 时 error 说明原因。
    public JsonObject? BuildConfig(out string error, out string? path)
    {
        error = ""; path = null;
        if (_nations.Count < 2) { error = "至少需要两个国家"; return null; }
        if (_nations.Select(n => Presets[n.Preset].Id).GroupBy(id => id).Any(g => g.Count() > 1))
        { error = "同一剧本国家重复出现"; return null; }
        if (!int.TryParse(_seed.Text.Trim(), out int seed) || seed < 0) { error = "种子须为非负整数"; return null; }
        var config = new JsonObject
        {
            ["map_size"] = (int)_mapSize.Value, ["seed"] = seed, ["max_turns"] = (int)_maxTurns.Value,
            ["ctx_window"] = (int)_ctxWindow.Value,
        };
        if(SelectedScenario["id"]!.ToString()!="")config["scenario"]=SelectedScenario["id"]!.ToString();
        var nations = new JsonArray();
        foreach (var draft in _nations)
        {
            var preset = Presets[draft.Preset];
            var nation = new JsonObject { ["id"] = preset.Id, ["name"] = draft.Name, ["client"] = Clients[draft.Client] switch { "规则AI" => "rule", "简易策略" => "sim", _ => "llm" } };
            var placement=SelectedScenario["nations"]!.AsArray().FirstOrDefault(n=>n!["id"]!.ToString()==preset.Id)?["position"];
            if(placement!=null)nation["position"]=placement.DeepClone();
            ParseObject(preset.Extra, "剧本预设", ref error, nation);
            if (draft.Client == 0)
            {
                nation["rule_ai"] = draft.RuleAi;
                nation["rule_strategy"] = Strategies[draft.Strategy];
                if (int.TryParse(draft.RuleSeed.Trim(), out int rs) && rs > 0) nation["rule_seed"] = rs;
            }
            else if (draft.Client == 2)
            {
                if (_profiles.Count == 0) { error = $"{draft.Name} 选择大模型但还没有 API 档案，请先在设置中添加"; return null; }
                var profile = _profiles[Math.Clamp(draft.Profile, 0, _profiles.Count - 1)];
                nation["provider"] = profile.Provider; nation["base_url"] = profile.BaseUrl;
                nation["api_key"] = profile.ApiKey; nation["model"] = draft.Model != "" ? draft.Model : profile.Model;
                nation["thinking"] = draft.Thinking; nation["reasoning_effort"] = draft.Effort;
                nation["temperature"] = double.TryParse(draft.Temperature, out var temp) ? temp : 0.3;
                nation["max_tokens"] = int.TryParse(draft.MaxTokens, out var tokens) ? tokens : 16384;
                nation["max_steps"] = int.TryParse(draft.MaxSteps, out var steps) ? steps : 28;
                ParseObject(profile.ExtraJson, "API 档案", ref error, nation);
            }
            ParseObject(draft.ExtraJson, $"{draft.Name} 的附加字段", ref error, nation);
            nations.Add(nation);
        }
        config["nations"] = nations;
        ParseObject(_globalExtra.Text, "全局附加", ref error, config);
        if((config["scenario"]?.ToString()??"")!=SelectedScenario["id"]!.ToString())error="全局附加 JSON 的 scenario 与所选剧本不一致";
        if (error != "") return null;
        Directory.CreateDirectory(Path.GetDirectoryName(_settingsPath)!);
        path = Path.Combine(Path.GetDirectoryName(_settingsPath)!, $"menu-config-{DateTime.Now:yyyyMMdd-HHmmss}.json");
        File.WriteAllText(path, config.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        return config;
    }
    private static void ParseObject(string json, string source, ref string error, JsonObject target)
    {
        if (string.IsNullOrWhiteSpace(json)) return;
        try
        {
            if (JsonNode.Parse(json) is JsonObject extra)
                foreach (var (key, value) in extra) target[key] = value?.DeepClone();
            else error = $"{source}必须是 JSON 对象 {{ … }}";
        }
        catch (JsonException) { error = $"{source}解析失败"; }
    }

    // -------------------------------------------------------------------- load
    private Control BuildLoad(Control parent)
    {
        var panel = Section(parent);
        var scroll = new ScrollContainer { SizeFlagsVertical = SizeFlags.ExpandFill, HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled };
        panel.AddChild(scroll);
        _saveList = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        _saveList.AddThemeConstantOverride("separation", 6); scroll.AddChild(_saveList);
        panel.AddChild(BackButton());
        return panel;
    }
    private VBoxContainer _saveList = null!;
    private void RefreshSaves()
    {
        foreach (Node child in _saveList.GetChildren()) { _saveList.RemoveChild(child); child.QueueFree(); }
        var directory = Path.Combine(Path.GetDirectoryName(_settingsPath)!,"desktop");
        var saves = (Directory.Exists(directory)?Directory.GetDirectories(directory):Array.Empty<string>())
            .Select(dir => (dir, session: Path.Combine(dir, "session.json"), config: Path.Combine(dir, "config.json")))
            .Where(entry => File.Exists(entry.session) && File.Exists(entry.config))
            .OrderByDescending(entry => File.GetLastWriteTime(entry.session)).Take(30).ToList();
        if (saves.Count == 0)
            _saveList.AddChild(new Label { Text = "archive/desktop 下还没有存档", Modulate = new Color("a4b09c") });
        foreach (var entry in saves)
        {
            string caption = $"{Path.GetFileName(entry.dir)}";
            try
            {
                var saved=JsonNode.Parse(File.ReadAllText(entry.config));
                caption+=" · "+(_scenarioCatalog.FirstOrDefault(s=>s!["id"]!.ToString()==(saved?["scenario"]?.ToString()??""))?["name"]?.ToString()??saved?["scenario"]?.ToString()??"未知剧本");
                if (saved?["nations"] is JsonArray rows)
                    caption += $" · {rows.Count} 国 · {string.Join("、", rows.Select(r => r?["name"]?.ToString()))}";
            }
            catch (IOException) { }
            catch (JsonException) { }
            var row = new HBoxContainer();
            var label = new Label { Text = caption,TooltipText=caption,TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis, SizeFlagsHorizontal = SizeFlags.ExpandFill };
            label.AddThemeFontSizeOverride("font_size", 18); row.AddChild(label);
            var resume = new Button { Text = "续看", FocusMode = FocusModeEnum.None };
            string session = entry.session, config = entry.config;
            resume.Pressed += () => StartRequested?.Invoke(config, session);
            row.AddChild(resume);
            _saveList.AddChild(row);
        }
    }

    // ---------------------------------------------------------------- settings
    private Control BuildSettings(Control parent)
    {
        var panel = Section(parent);
        var intro = new Label { Text = "API 档案：名称 + 接入参数保存在 archive/desktop-settings.json（不入库的本地文件），新对局里给大模型国家选用。" };
        intro.AddThemeFontSizeOverride("font_size", 18); intro.Modulate = new Color("a4b09c");
        intro.AutowrapMode = TextServer.AutowrapMode.WordSmart; panel.AddChild(intro);
        var scroll = new ScrollContainer { SizeFlagsVertical = SizeFlags.ExpandFill, HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled };
        panel.AddChild(scroll);
        _profileList = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        _profileList.AddThemeConstantOverride("separation", 8); scroll.AddChild(_profileList);
        var addProfile = new Button { Text = "+ 添加档案", FocusMode = FocusModeEnum.None };
        addProfile.Pressed += () => { _profiles.Add(new ApiProfile()); RefreshProfiles(); };
        panel.AddChild(addProfile);
        var save = new Button { Text = "保存设置", FocusMode = FocusModeEnum.None };
        save.Pressed += () => { var message = SaveSettings(); _hint.Text = message; };
        panel.AddChild(save);
        panel.AddChild(BackButton());
        return panel;
    }
    private void RefreshProfiles()
    {
        foreach (Node child in _profileList.GetChildren()) { _profileList.RemoveChild(child); child.QueueFree(); }
        foreach (var profile in _profiles)
        {
            var grid = new GridContainer { Columns = 2,SizeFlagsHorizontal=SizeFlags.ExpandFill };
            grid.AddThemeConstantOverride("h_separation", 8); grid.AddThemeConstantOverride("v_separation", 4);
            grid.AddChild(FieldLabel("名称"));
            var nameEdit = new LineEdit { SizeFlagsHorizontal=SizeFlags.ExpandFill, Text = profile.Name };
            nameEdit.TextChanged += v => profile.Name = v; grid.AddChild(nameEdit);
            grid.AddChild(FieldLabel("provider"));
            var provider = new OptionButton { FocusMode = FocusModeEnum.None };
            foreach (var p in new[] { "openai", "anthropic" }) provider.AddItem(p);
            provider.Selected = Math.Max(0, new[] { "openai", "anthropic" }.ToList().IndexOf(profile.Provider));
            provider.ItemSelected += i => profile.Provider = new[] { "openai", "anthropic" }[(int)i];
            grid.AddChild(provider);
            grid.AddChild(FieldLabel("base_url"));
            var url = new LineEdit { SizeFlagsHorizontal=SizeFlags.ExpandFill, Text = profile.BaseUrl }; url.TextChanged += v => profile.BaseUrl = v; grid.AddChild(url);
            grid.AddChild(FieldLabel("api_key"));
            var key = new LineEdit { SizeFlagsHorizontal=SizeFlags.ExpandFill, Text = profile.ApiKey, Secret = true }; key.TextChanged += v => profile.ApiKey = v; grid.AddChild(key);
            grid.AddChild(FieldLabel("默认模型"));
            var model = new LineEdit { SizeFlagsHorizontal=SizeFlags.ExpandFill, Text = profile.Model }; model.TextChanged += v => profile.Model = v; grid.AddChild(model);
            grid.AddChild(FieldLabel("附加 JSON"));
            var extra = new LineEdit { SizeFlagsHorizontal=SizeFlags.ExpandFill, Text = profile.ExtraJson, PlaceholderText = "如 {\"timeout\":120}" };
            extra.TextChanged += v => profile.ExtraJson = v; grid.AddChild(extra);
            var row = new HBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };
            row.AddChild(grid);
            var remove = new Button { Text = "−", FocusMode = FocusModeEnum.None };
            remove.Pressed += () => { _profiles.Remove(profile); RefreshProfiles(); };
            row.AddChild(remove);
            _profileList.AddChild(row);
        }
    }
    private Button BackButton()
    {
        var back = new Button { Text = "← 返回", FocusMode = FocusModeEnum.None };
        back.Pressed += () => OpenPanel(_home);
        return back;
    }
    private static Label FieldLabel(string text)
    {
        var label = new Label { Text = text };
        label.AddThemeFontSizeOverride("font_size", 18);
        return label;
    }

    public void LoadSettings()
    {
        _profiles.Clear();
        try
        {
            if (File.Exists(_settingsPath) && JsonNode.Parse(File.ReadAllText(_settingsPath))?["profiles"] is JsonArray rows)
                foreach (var row in rows) if (row is JsonObject o) _profiles.Add(ApiProfile.FromJson(o));
        }
        catch (IOException) { }
        catch (JsonException) { }
    }
    public void ReviewLlmCard()
    {
        _profiles.Clear();_profiles.Add(new ApiProfile { Name="离线界面样例",Model="DeepSeek-Offline",BaseUrl="http://offline.invalid",ApiKey="unused" });
        foreach(var (box,draft) in _profilePickers)RefreshProfileOptions(box,draft);
        var select=_rows[0].Row.FindChild("NationClient",true,false) as OptionButton;
        select!.Select(2);select.EmitSignal(OptionButton.SignalName.ItemSelected,2L);
    }
    public void ValidateVisibleLayout()
    {
        void Check(Node node)
        {
            if(node is Label label&&label.IsVisibleInTree()&&label.GetParent() is HBoxContainer&&label.Size.Y>72)
                throw new Exception("横行文字被挤成竖排："+label.Text);
            if(node is ScrollContainer scroll&&scroll.IsVisibleInTree()&&scroll.GetHScrollBar().MaxValue>scroll.GetHScrollBar().Page+1)
                throw new Exception("菜单内容出现横向溢出");
            foreach(var child in node.GetChildren())Check(child);
        }
        Check(this);
    }
    public string SaveSettings()
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_settingsPath)!);
            var root = new JsonObject();
            var rows = new JsonArray();
            foreach (var profile in _profiles) rows.Add(profile.ToJson());
            root["profiles"] = rows;
            File.WriteAllText(_settingsPath, root.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
            return $"已保存 {_profiles.Count} 个档案";
        }
        catch (Exception e) { return "保存失败：" + e.Message; }
    }
}
