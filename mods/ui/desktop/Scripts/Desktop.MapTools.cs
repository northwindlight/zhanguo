using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using static WorldMap;

public partial class Desktop
{
    private bool _simpleTerrain,_rightClickReturn;
    private HBoxContainer _mapTools=null!,_guideTools=null!;
    private PanelContainer _terrainLegend=null!;
    private Button _terrainModeButton=null!,_guideButton=null!;
    private CheckButton _rightReturnToggle=null!;
    private OptionButton _guidePicker=null!;
    private string _lastOperationPage="build",_guideReturnPage="build",_guideDoc="manual";
    private int _guideReturnScroll;
    private int? _restoreRtsScroll;
    private readonly Dictionary<string,string> _lastRtsKinds=new();
    private string? _returnRtsFocus;
    private readonly Dictionary<string,JsonObject> _guideCache=new();

    private void BuildMapTools()
    {
        _mapTools=new HBoxContainer { ZIndex=7 };_mapTools.AddThemeConstantOverride("separation",8);AddChild(_mapTools);
        _terrainModeButton=Button(_mapTools,"地形图",()=>{_simpleTerrain=!_simpleTerrain;ApplyDisplayOptions(true);});
        _terrainModeButton.Name="TerrainMode";_terrainModeButton.ToggleMode=true;HumanArt.Style(_terrainModeButton,"map");
        _terrainModeButton.TooltipText="普通地图／简易地形图；保留国界、军队、玩家视野与带日期的历史地图。";
        _guideButton=Button(_mapTools,"查询指南",OpenHumanGuide);_guideButton.Name="OpenGuide";
        _guideButton.Icon=MapIcons.Texture("guide");HumanArt.Style(_guideButton);_guideButton.AddThemeConstantOverride("icon_max_width",22);
        _terrainLegend=new PanelContainer { Name="TerrainLegend",ZIndex=7 };_terrainLegend.AddThemeStyleboxOverride("panel",HumanArt.Box("panel"));AddChild(_terrainLegend);
        var box=new VBoxContainer();_terrainLegend.AddChild(box);
        var flow=new HFlowContainer();flow.AddThemeConstantOverride("h_separation",10);flow.AddThemeConstantOverride("v_separation",5);box.AddChild(flow);
        foreach(var (id,label) in new[]{("plain","平原"),("forest","森林"),("hill","丘陵"),("mountain","山地"),("desert","沙漠"),("high-mountain","高山"),("ocean","海洋")})
        {
            var item=new HBoxContainer();flow.AddChild(item);
            item.AddChild(new TextureRect { Texture=MapIcons.Texture(id),CustomMinimumSize=new Vector2(20,20),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered });
            var text=new Label { Text=label,Modulate=MapIcons.Color(id).Lightened(.45f) };text.AddThemeFontSizeOverride("font_size",14);item.AddChild(text);
        }
        var note=new Label { Text="暗色覆盖：历史地图 · 未知区域不显示地形",AutowrapMode=TextServer.AutowrapMode.WordSmart,Modulate=new Color("b6c6b1") };
        note.AddThemeFontSizeOverride("font_size",12);box.AddChild(note);
        ApplyMapPreferences();LayoutMapTools();
    }
    private void ApplyMapPreferences()
    {
        _map.SetTerrainMode(_simpleTerrain);
        if(_rightReturnToggle!=null)_rightReturnToggle.SetPressedNoSignal(_rightClickReturn);
        if(_terrainModeButton==null)return;
        _terrainModeButton.SetPressedNoSignal(_simpleTerrain);_terrainModeButton.Text=_simpleTerrain?"普通地图":"地形图";
        _terrainLegend.Visible=_simpleTerrain;LayoutMapTools();
    }
    private void LayoutMapTools()
    {
        if(_mapTools==null)return;
        _mapTools.Visible=!_pauseMenu.Visible;_guideButton.Visible=HumanMode;
        _mapTools.Position=new Vector2(16,Size.Y-(HumanMode?108:100));
        _terrainLegend.Visible=_simpleTerrain&&!_pauseMenu.Visible;
        _terrainLegend.Size=new Vector2(292,0);
        _terrainLegend.Position=new Vector2(16,_mapTools.Position.Y-_terrainLegend.Size.Y-8);
    }
    private bool ReturnToLastOperation()
    {
        if(!_rightClickReturn||!HumanMode||_manualArmyTarget||_replaceNextRoute||_pauseMenu.Visible||_closing)return false;
        string page=_lastOperationPage;
        _returnRtsFocus=_lastRtsKinds.GetValueOrDefault(page);
        OpenHumanPage(page);return true;
    }

    private void BuildGuideToolbar(VBoxContainer parent)
    {
        _guideTools=new HBoxContainer { Visible=false };parent.AddChild(_guideTools);
        var back=Button(_guideTools,"← 返回操作",CloseHumanGuide);back.Name="GuideBack";HumanArt.Style(back);
        _guidePicker=new OptionButton { Name="GuideDocument",SizeFlagsHorizontal=SizeFlags.ExpandFill };
        foreach(string title in new[]{"游戏说明书 · 建设与地形","基础指南","经济学手册","战争手册","战略手册"})_guidePicker.AddItem(title);
        _guideTools.AddChild(_guidePicker);_guidePicker.AddThemeFontSizeOverride("font_size",15);
        _guidePicker.ItemSelected+=i=>{_guideDoc=new[]{"manual","basics","economics","war","strategy"}[(int)i];RenderHumanPage();};
    }
    private void OpenHumanGuide()
    {
        if(!HumanMode)return;
        if(_humanPage=="guide"){CloseHumanGuide();return;}
        _guideReturnPage=_humanPage==""?"build":_humanPage;_guideReturnScroll=_humanScroll.ScrollVertical;
        OpenHumanPage("guide");
    }
    private void CloseHumanGuide()
    {
        if(_guideReturnPage is "build" or "recruit")_restoreRtsScroll=_guideReturnScroll;
        if(_guideReturnPage=="land")OpenHumanOutliner(false);else OpenHumanPage(_guideReturnPage);
        if(_restoreRtsScroll==null)Callable.From(()=>_humanScroll.ScrollVertical=_guideReturnScroll).CallDeferred();
    }
    private void RenderHumanGuide()
    {
        string doc=_guideDoc;int epoch=_rtsEpoch;
        void Show(JsonObject entry)
        {
            if(_humanPage!="guide"||doc!=_guideDoc||epoch!=_rtsEpoch)return;
            ClearHumanChildren(_humanContent);_humanScroll.ScrollVertical=0;
            HumanText(S(entry["title"]),true);
            var body=new RichTextLabel { Name="GuideBody",BbcodeEnabled=true,FitContent=true,ScrollActive=false,
                SelectionEnabled=true,SizeFlagsHorizontal=SizeFlags.ExpandFill,AutowrapMode=TextServer.AutowrapMode.WordSmart };
            body.AddThemeFontSizeOverride("normal_font_size",16);_humanContent.AddChild(body);
            string text=ReadableGuide(S(entry["body"]));
            text=Regex.Replace(text,@"(?m)^#{1,6} (.+)$","[b][color=#e8d797]$1[/color][/b]");
            body.Text=Regex.Replace(text,@"\*\*(.+?)\*\*","[b]$1[/b]");
        }
        if(_guideCache.TryGetValue(doc,out var cached)){Show(cached);return;}
        HumanText("正在读取本局指南…",true);
        HumanQuery("panel",new JsonObject { ["panel"]="manual",["doc"]=doc },row=>
        {var entry=row["data"]!.AsObject();_guideCache[doc]=entry;Show(entry);});
    }
    private static string ReadableGuide(string source)
    {
        var lines=source.Split('\n');var result=new System.Text.StringBuilder();
        for(int i=0;i<lines.Length;i++)
        {
            if(lines[i].StartsWith("本文件是手写讲义，放在"))continue;
            if(lines[i].TrimStart().StartsWith('|')&&i+1<lines.Length&&Regex.IsMatch(lines[i+1],@"^\s*\|[\s:|\-]+\|\s*$"))
            {
                string[] Cells(string line)=>line.Trim().Trim('|').Split('|').Select(c=>c.Trim()).ToArray();
                var header=Cells(lines[i]);i+=2;
                for(;i<lines.Length&&lines[i].TrimStart().StartsWith('|');i++)
                {
                    var cells=Cells(lines[i]);if(cells.Length==0)continue;
                    result.Append("**").Append(cells[0]).Append("**\n");
                    result.AppendLine(string.Join(" · ",cells.Skip(1).Select((v,n)=>(n+1<header.Length?header[n+1]+"：":"")+v))).AppendLine();
                }
                i--;continue;
            }
            result.AppendLine(lines[i]);
        }
        return Regex.Replace(result.ToString(),@"\[([^\]]+)\]\([^)]+\)","$1").Replace("[","[lb]").Replace("`","");
    }
}
