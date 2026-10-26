using Godot;
using System;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;

public partial class Desktop
{
    private async Task ReviewFonts(string path)
    {
        try
        {
            var backdrop=new ColorRect { Color=new Color("142420"),ZIndex=50 };
            backdrop.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);AddChild(backdrop);
            var margin=new MarginContainer { ZIndex=51 };
            margin.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
            foreach(string edge in new[]{"left","right","top","bottom"})margin.AddThemeConstantOverride("margin_"+edge,24);
            AddChild(margin);
            var row=new HBoxContainer();row.AddThemeConstantOverride("separation",24);margin.AddChild(row);
            var checks=new System.Collections.Generic.List<object>();
            foreach(var (title,font,size) in new[]{("现有 Fusion 12px / 显示 18",PixelTheme.LegacyFontPath,18),
                ("Z 工坊 16px / 显示 16",PixelTheme.FontPath,16),("Z 工坊 16px / 显示 20",PixelTheme.FontPath,20)})
            {
                var box=new VBoxContainer { Theme=PixelTheme.Create(size,font),SizeFlagsHorizontal=SizeFlags.ExpandFill };
                box.AddThemeConstantOverride("separation",16);row.AddChild(box);
                box.AddChild(new Label { Text=title,AutowrapMode=TextServer.AutowrapMode.WordSmart });
                box.AddChild(new Label { Text="秦国 · 第 12 回合\n当前库存 · 第 11 期净变化" });
                foreach(var (name,amount,change) in new[]{("金币","1800.5","+12.5"),("粮食","240","+24"),("装备","36","-3"),("补给","18","-8")})
                {
                    var line=new HBoxContainer();line.AddChild(new Label { Text=name,SizeFlagsHorizontal=SizeFlags.ExpandFill });
                    line.AddChild(new Label { Text=amount,SizeFlagsHorizontal=SizeFlags.ExpandFill,HorizontalAlignment=HorizontalAlignment.Right });
                    var delta=new Label { Text=change,SizeFlagsHorizontal=SizeFlags.ExpandFill,HorizontalAlignment=HorizontalAlignment.Right };
                    delta.Modulate=new Color(change.StartsWith('-')?"ff9891":"a2e4a6");line.AddChild(delta);box.AddChild(line);
                }
                box.AddChild(new Label { Text="秦 步兵×2 · HP 200/200\n地块防御 20% · 补给实耗 8/8" });
                box.AddChild(new Label { Text="征召与装备、军需与采集\n楚魏齐燕赵韩周 · 贸易亏损\n0.000001  -12.75  +18  1×" });
                box.AddChild(new Button { Text="继续观看 · 保存并退出" });
                box.AddChild(new Label { Text="我们会继续扩大粮食产出，先保障前线补给，再建设工厂。若本期缺少装备，将调整征兵和交易安排。\n\nThe army advances after this call.\n（离线排版样本，未调用模型）",AutowrapMode=TextServer.AutowrapMode.WordSmart });
                checks.Add(new { font,size,title });
            }
            await ToSignal(GetTree().CreateTimer(.4),SceneTreeTimer.SignalName.Timeout);
            await Screenshot(path);File.WriteAllText(path+".json",JsonSerializer.Serialize(checks,new JsonSerializerOptions { WriteIndented=true }));
            GD.Print("FONT_REVIEW_OK "+path);GetTree().Quit();
        }
        catch(Exception e){GD.PrintErr(e);GetTree().Quit(3);}
    }
}
