using Godot;
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Channels;
using System.Threading.Tasks;
using static WorldMap;

public partial class Desktop : Control
{
    private Process? _host;
    private readonly Channel<string> _inbox = Channel.CreateBounded<string>(64);
    private WorldMap _map = null!;
    private Label _status = null!;
    private RichTextLabel _detail = null!;
    private RichTextLabel _events = null!;
    private RichTextLabel _market = null!;
    private VBoxContainer _nations = null!;
    private Button _play = null!;
    private Button _step = null!;
    private Button _save = null!;
    private bool _running;
    private bool _closing;
    private bool _stopped;
    private string? _generation;
    private int _revision;
    private int _period;
    private string _selectedNation = "";
    private string? _smoke;
    private bool _capturing;
    private double _elapsed;
    private string _savePath = "";
    private bool _smokeStarted;

    public override void _Ready()
    {
        GetTree().AutoAcceptQuit = false;
        var theme = new Theme { DefaultFontSize = 15 };
        var font = new SystemFont { FontNames = new[] { "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", "sans-serif" } };
        theme.DefaultFont = font;
        Theme = theme;
        foreach (string type in new[] { "Button", "PanelContainer", "TabContainer" })
        {
            var style = new StyleBoxFlat { BgColor = new Color("243332"), BorderColor = new Color("4d5c4c"),
                ContentMarginLeft = 12, ContentMarginRight = 12, ContentMarginTop = 8, ContentMarginBottom = 8 };
            style.SetBorderWidthAll(1);
            theme.SetStylebox(type=="Button"?"normal":"panel", type, style);
        }
        var bg = new ColorRect { Color = new Color("101b1d"), MouseFilter = MouseFilterEnum.Ignore };
        bg.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);AddChild(bg);
        var margin = new MarginContainer();margin.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        foreach(var side in new[]{"left","right","top","bottom"}) margin.AddThemeConstantOverride("margin_"+side,16);
        AddChild(margin);
        var page = new VBoxContainer();page.AddThemeConstantOverride("separation",12);margin.AddChild(page);
        var top = new HBoxContainer();top.AddThemeConstantOverride("separation",12);page.AddChild(top);
        var title = new Label { Text="战 国", CustomMinimumSize=new(100,0) };title.AddThemeFontSizeOverride("font_size",30);title.AddThemeColorOverride("font_color",new Color("e4d8ae"));top.AddChild(title);
        top.AddChild(new Label {Text="观天下  /  上帝视角", SizeFlagsHorizontal=SizeFlags.ExpandFill});
        _status = new Label { Text="正在创建离线八国世界…" };top.AddChild(_status);
        _play = Button(top,"继续",()=>Send(_running?"pause":"resume"));
        _step = Button(top,"下一回合",()=>Send("step"));
        _save = Button(top,"保存",()=>Send("save"));
        var body = new HBoxContainer { SizeFlagsVertical=SizeFlags.ExpandFill };body.AddThemeConstantOverride("separation",12);page.AddChild(body);
        var left = new VBoxContainer { CustomMinimumSize=new(174,0) };body.AddChild(left);
        left.AddChild(Heading("列国"));
        var scroller = new ScrollContainer { SizeFlagsVertical=SizeFlags.ExpandFill, HorizontalScrollMode=ScrollContainer.ScrollMode.Disabled };
        left.AddChild(scroller);_nations = new VBoxContainer {SizeFlagsHorizontal=SizeFlags.ExpandFill};scroller.AddChild(_nations);
        var all = Button(left,"总览天下",()=>{_selectedNation="";_map.Fit();ShowTile(null);});all.TooltipText="F：回到全图";
        var borders = new CheckButton {Text="国界",ButtonPressed=true};left.AddChild(borders);borders.Toggled += v=>{_map.Borders=v;_map.QueueRedraw();};
        var units = new CheckButton {Text="军队",ButtonPressed=true};left.AddChild(units);units.Toggled += v=>{_map.Units=v;_map.QueueRedraw();};
        left.AddChild(new Label {Text="离线八国 · 规则 AI\n独立演示局\n上限 300 回合",Modulate=new Color("a4b09c")});
        _map = new WorldMap {SizeFlagsHorizontal=SizeFlags.ExpandFill,SizeFlagsVertical=SizeFlags.ExpandFill};body.AddChild(_map);_map.SelectionChanged=ShowTile;
        var right = new VBoxContainer {CustomMinimumSize=new(310,0)};body.AddChild(right);
        right.AddChild(Heading("山河与国势"));
        _detail=TextBox(right);_detail.CustomMinimumSize=new(310,260);_detail.SizeFlagsVertical=SizeFlags.ExpandFill;
        right.AddChild(Heading("天下市集"));_market=TextBox(right);_market.CustomMinimumSize=new(310,190);
        var eventsHeader = new HBoxContainer();page.AddChild(eventsHeader);eventsHeader.AddChild(Heading("天下纪事"));eventsHeader.AddChild(new Label {Text="  结算事实 · 最近 160 条",Modulate=new Color("9aaa9e")});
        _events=TextBox(page);_events.CustomMinimumSize=new(0,145);_events.ScrollFollowing=true;
        page.AddChild(new Label {Text="空格：暂停 / 继续     F：全图     点击国名：聚焦     关闭窗口：完成本回合并保存",Modulate=new Color("93a398")});
        var args=OS.GetCmdlineUserArgs();
        for(int i=0;i<args.Length-1;i++)if(args[i]=="--smoke-output")_smoke=args[i+1];
        try { StartHost(); } catch(Exception e) { Error(e.Message); }
    }

    private static Label Heading(string text)
    {
        var label=new Label {Text=text};label.AddThemeColorOverride("font_color",new Color("ddcf9d"));label.AddThemeFontSizeOverride("font_size",18);return label;
    }
    private static Button Button(BoxContainer box,string text,Action action)
    {
        var b=new Button{Text=text,FocusMode=FocusModeEnum.None};b.Pressed+=action;box.AddChild(b);return b;
    }
    private static RichTextLabel TextBox(BoxContainer parent)
    {
        var text=new RichTextLabel {BbcodeEnabled=false,SelectionEnabled=true,SizeFlagsHorizontal=SizeFlags.ExpandFill};parent.AddChild(text);return text;
    }

    private void StartHost()
    {
        string root=OS.GetEnvironment("ZHANGUO_ROOT");
        if(root=="")root=Path.GetFullPath(Path.Combine(ProjectSettings.GlobalizePath("res://"),"../../.."));
        string python=OS.GetEnvironment("ZHANGUO_PYTHON");if(python=="")python=OS.GetName()=="Windows"?"python":"python3";
        string output=Path.Combine(root,"archive","desktop",DateTime.Now.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N")[..6]);
        var info=new ProcessStartInfo(python) {WorkingDirectory=root,UseShellExecute=false,RedirectStandardInput=true,
            RedirectStandardOutput=true,RedirectStandardError=true,CreateNoWindow=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
        info.Environment["PYTHONIOENCODING"]="utf-8";
        foreach(string arg in new[]{"-u","-m","ui.desktop","--output",output})info.ArgumentList.Add(arg);
        _host=Process.Start(info) ?? throw new Exception("无法启动 Python 宿主");
        _host.StandardInput.AutoFlush=true;
        _=ReadOutput();
        _=Task.Run(async()=>{while(await _host.StandardError.ReadLineAsync() is string line)GD.PrintErr(line);});
    }

    private async Task ReadOutput()
    {
        try
        {
            while(await _host!.StandardOutput.ReadLineAsync() is string line)await _inbox.Writer.WriteAsync(line);
            await _host.WaitForExitAsync();
            await _inbox.Writer.WriteAsync(JsonSerializer.Serialize(new {type="exit",code=_host.ExitCode}));
        }
        catch(Exception e){await _inbox.Writer.WriteAsync(JsonSerializer.Serialize(new {type="error",message=e.Message}));}
    }

    private void Send(string command)
    {
        if(_stopped||_host==null)return;
        try
        {
            _host.StandardInput.WriteLine(JsonSerializer.Serialize(new {command}));
            if(command is "pause" or "stop" or "save")_status.Text=command=="pause"?"暂停请求已排队 · 等待回合保存":command=="stop"?"正在完成回合并保存…":"等待回合边界保存…";
            _play.Disabled=true;_step.Disabled=true;
        }
        catch(Exception e){Error(e.Message);}
    }

    private void Error(string message)
    {
        _status.Text="宿主错误";_events.Text+="\n"+message;
        _closing=false;
        _running=false;_play.Disabled=false;_step.Disabled=false;
        GD.PrintErr(message);
    }

    public override void _Process(double delta)
    {
        _elapsed+=delta;
        for(int i=0;i<16 && _inbox.Reader.TryRead(out var line);i++)
        {
            try{Receive(JsonNode.Parse(line)!.AsObject());}catch(Exception e){Error("协议解析失败："+e.Message);}
        }
        if(_smoke!=null && _elapsed>60){GD.PrintErr("DESKTOP_SMOKE_TIMEOUT");GetTree().Quit(2);}
    }

    private void Receive(JsonObject row)
    {
        string type=S(row["type"]);
        if(type=="exit")
        {
            _stopped=true;
            if(_closing && I(row["code"])==0)GetTree().Quit();
            else Error("Python 宿主退出，代码 "+row["code"]);
            return;
        }
        if(type=="error"){Error(S(row["message"]));_play.Disabled=false;return;}
        if(S(row["protocol"])!="zhanguo.desktop/1")throw new Exception("不支持的协议版本");
        string generation=S(row["generation"]);int revision=I(row["revision"]);
        if(_generation==null)_generation=generation;
        if(generation!=_generation||revision<=_revision)return;
        _revision=revision;
        if(type=="snapshot")
        {
            var frame=row["frame"]!.AsObject();_period=I(frame["period"]);_map.SetFrame(frame);
            UpdatePanels(frame);
            if(_smoke!=null && _period>=3 && !_capturing){_capturing=true;_=Capture();}
        }
        if(type=="status")
        {
            string state=S(row["state"]);_running=state is "running" or "advancing";
            _savePath=S(row["save"]);
            string status=state switch {"running"=>"推演中","advancing"=>"列国行动中","paused"=>"已暂停","stopped"=>"已保存并停止",_=>state};
            _status.Text=$"第 {I(row["period"])} 回合 · {status} · 已存 {row["saved_period"]}";
            _status.TooltipText=_savePath;
            _play.Text=_running?"暂停":"继续";
            _play.Disabled=_closing;
            _step.Disabled=_closing||_running;
            _save.Disabled=_closing;
            if(_smoke!=null && !_smokeStarted && state=="paused") {_smokeStarted=true;Send("resume");}
        }
    }

    private void UpdatePanels(JsonObject frame)
    {
        foreach(Node child in _nations.GetChildren()){_nations.RemoveChild(child);child.QueueFree();}
        foreach(var n in frame["nations"]!.AsArray())
        {
            string id=S(n!["id"]);
            var button=Button(_nations,$"{n["name"]}    {n["land"]} 地",()=>{_selectedNation=id;_map.FocusNation(id);ShowTile(null);});
            button.Modulate=_map.NationColor(id);
        }
        _market.Text=string.Join("\n",frame["market"]!.AsArray().Select(p=>$"{p!["name"],-6}   {F(p["price"]):0.00}"))+
            $"\n\n储蓄利率 {F(frame["bank"]!["rate"]):P1} · 贷款 {F(frame["bank"]!["loan_rate"]):P1}";
        _events.Text=string.Join("\n",frame["events"]!.AsArray().Select(e=>S(e!["text"])));
        if(_map.Selected.X<0 || _selectedNation!="")ShowTile(null);
    }

    private void ShowTile(JsonObject? tile)
    {
        if(_map.Frame==null)return;
        if(tile!=null)
        {
            _selectedNation="";
            var p=tile["position"]!;string owner=S(tile["owner"]);
            var units=_map.Frame["armies"]!.AsArray().Where(a=>I(a!["x"])==I(p[0])&&I(a["y"])==I(p[1])).ToArray();
            _detail.Text=$"{tile["name"]}  ({p[0]}, {p[1]})\n{tile["terrain_name"]} · {(_map.Frame["names"]![owner]?.ToString()??"无主之地")}\n\n建筑\n"+
                (tile["building_labels"]!.AsArray().Count==0?"尚无建筑":string.Join("\n",tile["building_labels"]!.AsArray().Select(S)))+
                "\n\n军队\n"+(units.Length==0?"无已物化军队":string.Join("\n",units.Select(a=>$"{a!["label"]} #{a["id"]}  HP {a["hp"]}")))+
                "\n\n图标使用通用中世纪素材，\n建筑类型以此处文字为准。";
            return;
        }
        var nation=_map.Frame["nations"]!.AsArray().FirstOrDefault(n=>S(n!["id"])==_selectedNation);
        if(nation==null){_detail.Text="俯瞰山河，静观列国兴衰。\n\n点击地块查看地形、建筑和驻军。\n点击左侧国名查看国力。\n\n国界颜色表示当前归属；\n军旗数字表示本格真实军队数。\n无主守军按规则在遭遇时物化。\n\n本版采用 Mini World 像素素材。";return;}
        _detail.Text=$"{nation["name"]}国 · 第 {_period} 回合\n国土 {nation["land"]} 格\n总资产 {F(nation["assets"]):0.0}\n\n"+
            string.Join("\n",nation["stocks"]!.AsArray().Select(s=>$"{s!["name"]}    {s["amount"]}"))+"\n\n军队\n"+
            (nation["units"]!.AsObject().Count==0?"暂无军队":string.Join("  ",nation["units"]!.AsObject().Select(u=>$"{u.Key} {u.Value}")));
    }

    private async Task Capture()
    {
        Send("pause");
        await ToSignal(GetTree().CreateTimer(1.2),SceneTreeTimer.SignalName.Timeout);
        var first=_map.Frame!["tiles"]!.AsArray().First(t=>S(t!["owner"])=="scenario:qin")!;
        _map.FocusNation("scenario:qin");
        var cell=new Vector2I(I(first["position"]![0]),I(first["position"]![1]));
        var click=_map.CellCenter(cell);
        _map._GuiInput(new InputEventMouseButton {ButtonIndex=MouseButton.Left,Pressed=true,Position=click});
        _map._GuiInput(new InputEventMouseButton {ButtonIndex=MouseButton.Left,Pressed=false,Position=click});
        if(_map.Selected!=cell || !_detail.Text.Contains(S(first["name"])))
        {
            GD.PrintErr("DESKTOP_SMOKE_SELECTION_FAILED");GetTree().Quit(3);return;
        }
        _map.Selected=new Vector2I(-1,-1);ShowTile(null);
        _map.Fit();
        await ToSignal(RenderingServer.Singleton,RenderingServer.SignalName.FramePostDraw);
        string path=_smoke!;
        GetViewport().GetTexture().GetImage().SavePng(path);
        File.WriteAllText(path+".json",JsonSerializer.Serialize(new {period=_period,tiles=_map.TileCount,armies=_map.ArmyCount,save=_savePath}));
        GD.Print($"DESKTOP_SMOKE_OK period={_period} tiles={_map.TileCount} armies={_map.ArmyCount}");
        _closing=true;Send("stop");
    }

    public override void _UnhandledKeyInput(InputEvent input)
    {
        if(input is not InputEventKey key||!key.Pressed||key.Echo)return;
        if(key.Keycode==Key.F)_map.Fit();
        if(key.Keycode==Key.Space && !_closing)Send(_running?"pause":"resume");
    }
    public override void _Notification(int what)
    {
        if(what==NotificationWMCloseRequest)
        {
            if(_stopped||_host==null){GetTree().Quit();return;}
            if(_closing)return;
            _closing=true;Send("stop");
        }
    }
    public override void _ExitTree()
    {
        try{if(_host!=null&&!_host.HasExited)_host.StandardInput.Close();}catch(InvalidOperationException){}
        _host?.Dispose();
    }
}
