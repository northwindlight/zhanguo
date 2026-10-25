using Godot;
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using static WorldMap;

public partial class Desktop
{
    // Runs in the exported executable on Windows CI. It loads the actual .NET
    // export, then drives the bundled interpreter through a full offline turn.
    // No graphics device, system Python, installed SDK, or model key is needed.
    private async Task PortableCheck(string report)
    {
        Process? child=null;
        try
        {
            if(!_runtime.Portable)throw new Exception("便携包检查须使用导出的游戏。");
            string output=Path.Combine(_runtime.DataRoot,"checks",Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            child=Process.Start(_runtime.Host(output))??throw new Exception("内置 Python 无法启动");
            var stderr=child.StandardError.ReadToEndAsync();
            using var deadline=new CancellationTokenSource(TimeSpan.FromSeconds(90));
            bool initial=false,complete=false,stopped=false;
            int phases=0;
            while(await child.StandardOutput.ReadLineAsync(deadline.Token) is string line)
            {
                var row=JsonNode.Parse(line)!.AsObject();string type=S(row["type"]);
                if(type=="error")throw new Exception(S(row["message"]));
                if(type=="timeline")phases+=row["beats"]!.AsArray().Count;
                if(type=="snapshot")
                {
                    var frame=row["frame"]!;
                    if(frame["tiles"]!.AsArray().Count!=576||frame["names"]!.AsObject().Count!=8)
                        throw new Exception("便携包地图或国家数量不完整");
                    if(frame["clients"]!.AsArray().Any(c=>S(c!["kind"])!="rule"||S(c["version"])!="v13"))
                        throw new Exception("便携包默认局没有使用离线 v13");
                    if(!initial)
                    {
                        if(frame["armies"]!.AsArray().Count!=0)throw new Exception("默认开局有额外赠兵");
                        initial=true;await child.StandardInput.WriteLineAsync("{\"command\":\"step\"}");await child.StandardInput.FlushAsync();
                    }
                    else if(I(frame["period"])==1)
                    {
                        complete=true;await child.StandardInput.WriteLineAsync("{\"command\":\"stop\"}");await child.StandardInput.FlushAsync();child.StandardInput.Close();
                    }
                }
                if(type=="status"&&S(row["state"])=="stopped")stopped=true;
            }
            await child.WaitForExitAsync(deadline.Token);string errors=await stderr;
            if(child.ExitCode!=0||!initial||!complete||!stopped||phases==0||!File.Exists(Path.Combine(output,"session.json")))
                throw new Exception("便携包完整回合、保存或退出检查失败："+errors);
            var result=new { packaged=true,platform=OS.GetName(),bundled_python=_runtime.Python,period=1,nations=8,tiles=576,
                rule_ai="v13",initial_armies=0,timeline_beats=phases,saved=true,host_exit=child.ExitCode };
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(report))!);
            File.WriteAllText(report,JsonSerializer.Serialize(result,new JsonSerializerOptions{WriteIndented=true}));
            GD.Print("PORTABLE_CHECK_OK "+report);GetTree().Quit();
        }
        catch(Exception e){GD.PrintErr(e);GetTree().Quit(3);}
        finally
        {
            if(child!=null){if(!child.HasExited)child.Kill(entireProcessTree:true);child.Dispose();}
        }
    }
}
