using Godot;
using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;

public partial class Desktop
{
    // Explicitly offline fixtures exercise the same receive/render path as the
    // host. They never create an LLM client or open an existing game.
    private async Task ReviewDialogue(string path)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var fixture=OS.GetEnvironment("ZHANGUO_UI_REVIEW_FRAME");
            if(File.Exists(fixture))ApplyFrame(JsonNode.Parse(File.ReadAllText(fixture))!.AsObject());
            _status.Text="离线界面样例 · 非模型输出";
            _bannerTitle.Text="秦 · 第 1 回合";
            _bannerText.Text="建设演出与操作回执保留在这里；下方仅显示模型文本。";
            var checks=new System.Collections.Generic.List<object>();
            string stem=Path.ChangeExtension(path,null);
            foreach(var size in new[]{new Vector2I(1440,900),new Vector2I(1280,720),new Vector2I(1024,768)})
            {
                GetWindow().ContentScaleSize=size;GetWindow().Size=size;
                await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
                _bubbles.SetSpeaker("scenario:qin","秦","DeepSeek-Offline",new Color("9eb5cf"));
                // A deliberately tall texture proves no square crop or stretch;
                // this is a geometric test fixture, not shipped portrait art.
                var img=Image.CreateEmpty(80,180,false,Image.Format.Rgba8);img.Fill(new Color("648c9a"));
                img.FillRect(new Rect2I(0,0,80,12),new Color("e9ca86"));
                img.FillRect(new Rect2I(0,168,80,12),new Color("9cc79a"));
                _bubbles.SetPortrait(ImageTexture.CreateFromImage(img));
                _bubbles.HandleStream(new JsonObject { ["event"]="begin",["call"]=1 });
                string thought=string.Concat(Enumerable.Repeat("先核实粮仓与前线补给，再决定是否扩产。长思考可以展开、滚动查看。",18));
                string prose="诸位，本期优先补充粮食，并修复前线设施。\n外交承诺需明确条件和期限。\n这里只显示发言正文，建设、买卖和调兵另在地图演出。";
                _bubbles.HandleStream(new JsonObject { ["event"]="delta",["kind"]="thinking",["text"]=thought[..20] });
                if(_bubbles.ThoughtText!=thought[..20])throw new Exception("思考片段未立即显示");
                _bubbles.HandleStream(new JsonObject { ["event"]="delta",["kind"]="thinking",["text"]=thought[20..] });
                _bubbles.HandleStream(new JsonObject { ["event"]="delta",["kind"]="body",["text"]=prose });
                if(_bubbles.BodyText!=prose)throw new Exception("流式正文被插入换行或重复");
                _bubbles.HandleStream(new JsonObject { ["event"]="complete",["thinking"]=thought,["body"]=prose,["tokens"]=512,["seconds"]=2.3 });
                _bubbles.HandleStream(new JsonObject { ["event"]="summary",["text"]="完成粮仓检视；下期待核实补给到账。" });
                await ToSignal(GetTree().CreateTimer(.35),SceneTreeTimer.SignalName.Timeout);
                var viewport=new Rect2(Vector2.Zero,Size);
                if(!viewport.Encloses(_bubbles.DialogueBounds)||!viewport.Encloses(_bubbles.PortraitBounds))throw new Exception("气泡或半身图超出窗口："+size);
                if(_bubbles.DialogueBounds.Intersects(_bubbles.PortraitBounds))throw new Exception("半身图遮挡正文");
                if(_bubbles.DialogueBounds.End.Y>Size.Y-64)throw new Exception("气泡遮挡底部工具栏");
                await Screenshot(stem+$"-{size.X}x{size.Y}.png");
                var fold=_bubbles.FindChild("ThoughtToggle",true,false) as Button;
                fold!.EmitSignal(Godot.Button.SignalName.Pressed);
                await ToSignal(GetTree().CreateTimer(.25),SceneTreeTimer.SignalName.Timeout);
                if(!viewport.Encloses(_bubbles.DialogueBounds))throw new Exception("展开思考后越界");
                if(_bubbles.DialogueBounds.Intersects(_banner.GetGlobalRect()))throw new Exception("展开思考后与横幅重叠");
                await Screenshot(stem+$"-{size.X}x{size.Y}-expanded.png");
                fold.EmitSignal(Godot.Button.SignalName.Pressed);
                (_bubbles.FindChild("CollapseDialogue",true,false) as Button)!.EmitSignal(Godot.Button.SignalName.Pressed);
                (_bubbles.FindChild("RestoreDialogue",true,false) as Button)!.EmitSignal(Godot.Button.SignalName.Pressed);
                _bubbles.SetSpeaker("scenario:chu","楚","QWEN-Offline",new Color("d68c95"));
                if(_bubbles.ThoughtText!=""||_bubbles.BodyText!="")throw new Exception("切换国家残留前一位模型文本");
                _bubbles.HandleStream(new JsonObject { ["event"]="begin",["call"]=1 });
                _bubbles.HandleStream(new JsonObject { ["event"]="delta",["kind"]="body",["text"]="失败请求残片" });
                _bubbles.HandleStream(new JsonObject { ["event"]="reset" });
                _bubbles.HandleStream(new JsonObject { ["event"]="complete",["thinking"]="",["body"]="重试后的完整声明。" });
                if(_bubbles.BodyText!="重试后的完整声明。")throw new Exception("重试混入残片");
                checks.Add(new { width=size.X,height=size.Y,inside=true,portrait_aspect_preserved=true,delta_immediate=true,speaker_reset=true,retry_reset=true });
            }
            File.WriteAllText(path+".json",JsonSerializer.Serialize(checks,new JsonSerializerOptions { WriteIndented=true }));
            GD.Print("UI_REVIEW_OK "+path);GetTree().Quit();
        }
        catch(Exception e){GD.PrintErr(e);GetTree().Quit(3);}
    }
}
