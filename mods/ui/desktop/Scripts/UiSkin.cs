using Godot;
using System.Collections.Generic;

// 「漆金」界面皮肤：漆黑底、青铜描边与回纹角饰、朱漆题签、思源宋体正文与毛笔标题。
// 素材由 scripts/build_lacquer_ui.py 生成，色板与之同步。装饰只放在边框和标题，数据区保持素净。
public static class UiSkin
{
    public const string BodyFontPath="res://assets/fonts/NotoSerifSC-zhanguo.woff2";
    public const string BrushFontPath="res://assets/fonts/MaShanZheng-zhanguo.woff2";
    public static readonly Color InkDeep=new("0c0907"),Ink=new("18110b"),Raised=new("22190f"),Hover=new("33261a"),
        Bronze=new("b48d4e"),BronzeLight=new("e2c27e"),BronzeDark=new("7a5c34"),BronzeDeep=new("4a3822"),
        Lacquer=new("8e2b1e"),LacquerLight=new("b63c27"),Paper=new("efe2c4"),PaperMuted=new("bcae90"),PaperDim=new("8c806b"),
        Good=new("abd08a"),Bad=new("f08f76"),Jade=new("7fb3a0"),Gold=new("f0d58a");

    private static readonly Dictionary<string,Texture2D> Textures=new();
    private static Font? _body,_bold,_brush;
    private static FontFile? _bodyFile;

    public static Texture2D Texture(string name)
    {
        if(!Textures.TryGetValue(name,out var value))Textures[name]=value=GD.Load<Texture2D>($"res://assets/ui-lacquer/{name}.svg");
        return value;
    }
    public static Texture2D Icon(string name)=>Texture("icon-"+name);
    public static bool HasIcon(string name)=>ResourceLoader.Exists($"res://assets/ui-lacquer/icon-{name}.svg");

    private static FontFile LoadFont(string path)
    {
        var font=GD.Load<FontFile>(path);
        font.Antialiasing=TextServer.FontAntialiasing.Gray;
        font.Hinting=TextServer.Hinting.Light;
        font.SubpixelPositioning=TextServer.SubpixelPositioning.Auto;
        font.MultichannelSignedDistanceField=false;font.GenerateMipmaps=false;
        return font;
    }
    private static FontFile BodyFile=>_bodyFile??=LoadFont(BodyFontPath);
    private static Font Weight(int weight)
    {
        var tag=TextServerManager.GetPrimaryInterface().NameToTag("wght");
        return new FontVariation { BaseFont=BodyFile,VariationOpentype=new Godot.Collections.Dictionary { { tag,weight } } };
    }
    /// <summary>正文：思源宋体 Medium。</summary>
    public static Font Body=>_body??=Weight(520);
    /// <summary>强调与数字标题：思源宋体 Bold。</summary>
    public static Font Bold=>_bold??=Weight(720);
    /// <summary>毛笔标题：马善政楷书，缺字回退宋体。</summary>
    public static Font Brush
    {
        get
        {
            if(_brush!=null)return _brush;
            var brush=LoadFont(BrushFontPath);
            brush.Fallbacks=new Godot.Collections.Array<Font>{Bold};
            return _brush=brush;
        }
    }

    public static StyleBoxTexture Box(string name,float margin=-1)
    {
        var (l,t,r,b)=name switch {
            "panel" or "panel-glass"=>(24,24,24,24),"dock"=>(32,14,32,10),"title"=>(22,6,22,6),"heading"=>(16,0,16,10),
            "card"=>(8,8,8,8),"tooltip"=>(10,10,10,10),"seal-button" or "seal-button-hover" or "seal-button-disabled"=>(14,12,14,12),
            _=>(8,8,8,8) };
        var box=new StyleBoxTexture { Texture=Texture(name),TextureMarginLeft=l,TextureMarginTop=t,TextureMarginRight=r,TextureMarginBottom=b };
        float m=margin;
        (box.ContentMarginLeft,box.ContentMarginTop,box.ContentMarginRight,box.ContentMarginBottom)=name switch {
            "panel" or "panel-glass"=>(m<0?20:m,m<0?16:m,m<0?20:m,m<0?16:m),
            "dock"=>(18,14,18,10),"title"=>(22,3,24,5),"heading"=>(2,0,2,9),"card"=>(10,8,10,8),"tooltip"=>(12,8,12,9),
            _=>(m<0?10:m,m<0?6:m,m<0?10:m,m<0?6:m) };
        return box;
    }
    public static StyleBoxFlat Flat(Color bg,Color border,int width=1,int margin=8)
    {
        var s=new StyleBoxFlat { BgColor=bg,BorderColor=border,ContentMarginLeft=margin,ContentMarginRight=margin,ContentMarginTop=margin*.6f,ContentMarginBottom=margin*.6f };
        s.SetBorderWidthAll(width);s.SetCornerRadiusAll(2);return s;
    }

    /// <summary>按钮样式。variant：""普通、"primary"朱漆主操作、"danger"危险、"seal"结束回合印、"nav"底栏导航。</summary>
    public static void Style(Button button,string icon="",string variant="")
    {
        if(button.TextureFilter==CanvasItem.TextureFilterEnum.ParentNode)button.TextureFilter=CanvasItem.TextureFilterEnum.LinearWithMipmaps;
        if(icon!=""&&HasIcon(icon))
        {
            button.Icon=Icon(icon);button.ExpandIcon=false;
            button.AddThemeConstantOverride("icon_max_width",variant=="nav"?30:22);button.AddThemeConstantOverride("h_separation",7);
        }
        string normal="button-normal",hover="button-hover",pressed="button-pressed",disabled="button-disabled";
        if(variant=="primary"){normal="button-primary";hover="button-primary-hover";pressed="button-primary-pressed";}
        else if(variant=="danger"){normal="button-danger";hover="button-primary-hover";pressed="button-primary-pressed";}
        else if(variant=="seal"){normal="seal-button";hover="seal-button-hover";pressed="button-primary-pressed";disabled="seal-button-disabled";}
        button.AddThemeStyleboxOverride("normal",Box(normal));button.AddThemeStyleboxOverride("hover",Box(hover));
        button.AddThemeStyleboxOverride("pressed",Box(pressed));button.AddThemeStyleboxOverride("hover_pressed",Box(pressed));
        button.AddThemeStyleboxOverride("disabled",Box(disabled));button.AddThemeStyleboxOverride("focus",Box("button-focus"));
        if(button.ToggleMode&&variant=="")
        {
            // 切换按钮的「按下」即「已选中」：青铜高亮框而非凹陷。
            button.AddThemeStyleboxOverride("pressed",Box("button-selected"));button.AddThemeStyleboxOverride("hover_pressed",Box("button-selected"));
            button.AddThemeColorOverride("font_pressed_color",Gold);
        }
        if(variant is "primary" or "danger" or "seal")
        {
            button.AddThemeColorOverride("font_color",Paper);button.AddThemeColorOverride("font_hover_color",new Color("fff4d6"));
            button.AddThemeColorOverride("font_pressed_color",Gold);
        }
    }
    /// <summary>当前页/已选中的导航按钮使用青铜高亮框。</summary>
    public static void Selected(Button button,bool on)
    {
        var old=button.GetThemeStylebox("normal");var box=Box(on?"button-selected":"button-normal");
        box.ContentMarginLeft=old.ContentMarginLeft;box.ContentMarginRight=old.ContentMarginRight;
        box.ContentMarginTop=old.ContentMarginTop;box.ContentMarginBottom=old.ContentMarginBottom;
        button.AddThemeStyleboxOverride("normal",box);
        if(on)button.AddThemeColorOverride("font_color",Gold);else button.RemoveThemeColorOverride("font_color");
    }

    /// <summary>毛笔标题，放在朱漆题签上。</summary>
    public static PanelContainer TitleBand(string text,out Label label,int size=22)
    {
        var band=new PanelContainer { MouseFilter=Control.MouseFilterEnum.Ignore };
        band.AddThemeStyleboxOverride("panel",Box("title"));
        label=new Label { Text=text,VerticalAlignment=VerticalAlignment.Center };
        label.AddThemeFontOverride("font",Brush);label.AddThemeFontSizeOverride("font_size",size);
        label.AddThemeColorOverride("font_color",new Color("fbeccb"));
        label.AddThemeColorOverride("font_shadow_color",new Color(0,0,0,.45f));label.AddThemeConstantOverride("shadow_offset_y",1);
        band.AddChild(label);return band;
    }
    /// <summary>小节标题：毛笔字 + 左侧朱漆菱形与渐隐青铜线。</summary>
    public static void Heading(Label label,int size=21)
    {
        label.AddThemeFontOverride("font",Brush);label.AddThemeFontSizeOverride("font_size",size);
        label.AddThemeColorOverride("font_color",BronzeLight);
        label.AddThemeStyleboxOverride("normal",Box("heading"));
    }

    /// <summary>旧版青绿色板的文字色映射到新色板，避免冷灰字压在暖漆底上。</summary>
    public static Color Text(string hex)=>hex.ToLowerInvariant() switch {
        "e5e5d2" or "e7deb4" or "dfe3da" or "e4d8ae" or "ddcf9d"=>Paper,
        "a2b8ac" or "a5b8ac" or "a7b8a9" or "a4b09c" or "a6b9ae" or "b4c2bb" or "a2ad9d" or "a7b6a1" or "a4b69f" or "b2c6b5" or "a9cddd" or "c0c9b2"=>PaperMuted,
        "cbdcae" or "d6e6c7" or "c6d4b4" or "b8d5b3"=>new Color("d9d2a6"),
        "c6e3ad" or "c9efa3" or "b8efad" or "a2e4a6"=>Good,
        "ffb6a4" or "e49181" or "e6b59b" or "e5b389" or "ffb49c"=>Bad,
        "d4b9a0"=>new Color("e0b48e"),
        "d6cba7" or "c9b98a" or "b8a884" or "b0a488"=>new Color("cdb98a"),
        _=>new Color(hex) };

    public static Theme CreateTheme(int size=16)
    {
        var theme=new Theme { DefaultFont=Body,DefaultFontSize=size };
        theme.SetConstant("line_spacing","Label",3);
        theme.SetConstant("line_separation","RichTextLabel",5);
        theme.SetColor("font_color","Label",Paper);
        theme.SetColor("default_color","RichTextLabel",Paper);
        theme.SetFont("bold_font","RichTextLabel",Bold);
        foreach(string type in new[]{"Button","OptionButton","MenuButton"})
        {
            theme.SetStylebox("normal",type,Box("button-normal"));theme.SetStylebox("hover",type,Box("button-hover"));
            theme.SetStylebox("pressed",type,Box("button-pressed"));theme.SetStylebox("hover_pressed",type,Box("button-pressed"));
            theme.SetStylebox("disabled",type,Box("button-disabled"));theme.SetStylebox("focus",type,Box("button-focus"));
            theme.SetColor("font_color",type,Paper);theme.SetColor("font_hover_color",type,new Color("fff4d6"));
            theme.SetColor("font_pressed_color",type,Gold);theme.SetColor("font_hover_pressed_color",type,Gold);
            theme.SetColor("font_focus_color",type,Paper);theme.SetColor("font_disabled_color",type,new Color("6e6455"));
            theme.SetColor("icon_disabled_color",type,new Color(1,1,1,.35f));
            theme.SetConstant("h_separation",type,7);
        }
        theme.SetIcon("arrow","OptionButton",Texture("arrow-down"));
        foreach(string type in new[]{"CheckBox","CheckButton"})
        {
            var empty=new StyleBoxEmpty { ContentMarginLeft=4,ContentMarginRight=4,ContentMarginTop=3,ContentMarginBottom=3 };
            foreach(var state in new[]{"normal","pressed","hover","hover_pressed","disabled","focus"})theme.SetStylebox(state,type,empty);
            theme.SetColor("font_color",type,Paper);theme.SetColor("font_hover_color",type,new Color("fff4d6"));
            theme.SetColor("font_pressed_color",type,Paper);theme.SetColor("font_hover_pressed_color",type,new Color("fff4d6"));
            theme.SetColor("font_disabled_color",type,new Color("6e6455"));theme.SetConstant("h_separation",type,8);
        }
        theme.SetIcon("checked","CheckBox",Texture("check-on"));theme.SetIcon("unchecked","CheckBox",Texture("check-off"));
        theme.SetIcon("checked_disabled","CheckBox",Texture("check-disabled"));theme.SetIcon("unchecked_disabled","CheckBox",Texture("check-disabled"));
        theme.SetIcon("radio_checked","CheckBox",Texture("radio-on"));theme.SetIcon("radio_unchecked","CheckBox",Texture("radio-off"));
        theme.SetIcon("checked","CheckButton",Texture("toggle-on"));theme.SetIcon("unchecked","CheckButton",Texture("toggle-off"));
        theme.SetIcon("checked_disabled","CheckButton",Texture("toggle-off"));theme.SetIcon("unchecked_disabled","CheckButton",Texture("toggle-off"));
        foreach(string type in new[]{"LineEdit","TextEdit","SpinBox"})
        {
            var field=Flat(new Color("100b08"),BronzeDark,1,8);
            var focus=Flat(new Color("140e0a"),BronzeLight,1,8);
            theme.SetStylebox("normal",type,field);theme.SetStylebox("focus",type,focus);theme.SetStylebox("read_only",type,Flat(Ink,BronzeDeep,1,8));
            theme.SetColor("font_color",type,Paper);theme.SetColor("font_placeholder_color",type,PaperDim);
            theme.SetColor("caret_color",type,BronzeLight);theme.SetColor("selection_color",type,new Color(.71f,.24f,.15f,.55f));
        }
        theme.SetStylebox("panel","PanelContainer",Box("card"));
        theme.SetStylebox("panel","Panel",Box("card"));
        theme.SetStylebox("panel","TooltipPanel",Box("tooltip"));
        theme.SetColor("font_color","TooltipLabel",Paper);theme.SetFontSize("font_size","TooltipLabel",15);
        theme.SetStylebox("panel","PopupMenu",Box("tooltip"));
        theme.SetStylebox("hover","PopupMenu",Flat(new Color("3a2a1a"),BronzeDark,1,6));
        theme.SetColor("font_color","PopupMenu",Paper);theme.SetColor("font_hover_color","PopupMenu",Gold);
        theme.SetColor("font_disabled_color","PopupMenu",new Color("6e6455"));
        theme.SetIcon("checked","PopupMenu",Texture("check-on"));theme.SetIcon("unchecked","PopupMenu",Texture("check-off"));
        theme.SetIcon("radio_checked","PopupMenu",Texture("radio-on"));theme.SetIcon("radio_unchecked","PopupMenu",Texture("radio-off"));
        theme.SetStylebox("panel","PopupPanel",Box("tooltip"));
        theme.SetStylebox("panel","AcceptDialog",new StyleBoxFlat { BgColor=Ink,ContentMarginLeft=16,ContentMarginRight=16,ContentMarginTop=12,ContentMarginBottom=12 });
        var border=Box("panel");border.ExpandMarginTop=32;border.ExpandMarginLeft=6;border.ExpandMarginRight=6;border.ExpandMarginBottom=6;
        theme.SetStylebox("embedded_border","Window",border);theme.SetStylebox("embedded_unfocused_border","Window",border);
        theme.SetFont("title_font","Window",Brush);theme.SetFontSize("title_font_size","Window",22);
        theme.SetColor("title_color","Window",BronzeLight);theme.SetConstant("title_height","Window",30);
        theme.SetIcon("close","Window",Icon("close"));theme.SetIcon("close_pressed","Window",Icon("close"));
        foreach(string bar in new[]{"VScrollBar","HScrollBar"})
        {
            var track=new StyleBoxFlat { BgColor=new Color(0,0,0,.25f) };track.SetCornerRadiusAll(3);
            var grab=new StyleBoxFlat { BgColor=BronzeDark };grab.SetCornerRadiusAll(3);
            var hi=new StyleBoxFlat { BgColor=Bronze };hi.SetCornerRadiusAll(3);
            if(bar=="VScrollBar"){track.ContentMarginLeft=track.ContentMarginRight=4;grab.ContentMarginLeft=grab.ContentMarginRight=4;}
            else{track.ContentMarginTop=track.ContentMarginBottom=4;grab.ContentMarginTop=grab.ContentMarginBottom=4;}
            theme.SetStylebox("scroll",bar,track);theme.SetStylebox("grabber",bar,grab);
            theme.SetStylebox("grabber_highlight",bar,hi);theme.SetStylebox("grabber_pressed",bar,hi);
        }
        var bg=new StyleBoxFlat { BgColor=new Color("0f0a07"),BorderColor=BronzeDeep };bg.SetBorderWidthAll(1);bg.SetCornerRadiusAll(2);
        var fill=new StyleBoxFlat { BgColor=LacquerLight };fill.SetCornerRadiusAll(2);
        theme.SetStylebox("background","ProgressBar",bg);theme.SetStylebox("fill","ProgressBar",fill);
        theme.SetColor("font_color","ProgressBar",Paper);
        var line=new StyleBoxLine { Color=BronzeDeep,Thickness=1 };
        theme.SetStylebox("separator","HSeparator",line);
        theme.SetStylebox("separator","VSeparator",new StyleBoxLine { Color=BronzeDeep,Thickness=1,Vertical=true });
        theme.SetStylebox("tab_selected","TabBar",Box("button-selected"));theme.SetStylebox("tab_unselected","TabBar",Box("button-normal"));
        theme.SetStylebox("tab_hovered","TabBar",Box("button-hover"));theme.SetStylebox("tab_disabled","TabBar",Box("button-disabled"));
        theme.SetStylebox("tab_selected","TabContainer",Box("button-selected"));theme.SetStylebox("tab_unselected","TabContainer",Box("button-normal"));
        theme.SetStylebox("tab_hovered","TabContainer",Box("button-hover"));theme.SetStylebox("panel","TabContainer",Box("card"));
        theme.SetColor("font_selected_color","TabBar",Gold);theme.SetColor("font_unselected_color","TabBar",PaperMuted);
        theme.SetColor("font_selected_color","TabContainer",Gold);theme.SetColor("font_unselected_color","TabContainer",PaperMuted);
        theme.SetStylebox("panel","ItemList",Flat(new Color("100b08"),BronzeDeep,1,6));
        theme.SetStylebox("selected","ItemList",Flat(new Color("3a2a1a"),BronzeLight,1,4));
        theme.SetStylebox("selected_focus","ItemList",Flat(new Color("3a2a1a"),BronzeLight,1,4));
        theme.SetStylebox("hovered","ItemList",Flat(new Color("2a1f15"),BronzeDark,1,4));
        theme.SetColor("font_color","ItemList",Paper);theme.SetColor("font_selected_color","ItemList",Gold);
        var grabber=Texture("radio-on");
        theme.SetIcon("grabber","HSlider",grabber);theme.SetIcon("grabber_highlight","HSlider",grabber);
        theme.SetStylebox("slider","HSlider",new StyleBoxFlat { BgColor=BronzeDark,ContentMarginTop=2,ContentMarginBottom=2 });
        return theme;
    }
}
