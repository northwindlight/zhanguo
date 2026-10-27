using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json.Nodes;

namespace Zhanguo.Desktop.Layout;

public sealed record LedgerStock(string Id,string Name,double Amount,double Change);
public sealed record LedgerPower(double Capacity,double? Generated,double Demand,bool? PreviousShort,int FuelShortPlants);
public sealed record NationLedgerView(string Actor,string Name,int Period,int FlowPeriod,IReadOnlyList<LedgerStock> Stocks)
{ public LedgerPower? Power {get;init;} }

// Reads the replayed frame, including action-time account deltas; never reads
// the pending end-of-batch frame or guesses income from inventory differences.
public static class NationLedger
{
    public static NationLedgerView? Read(JsonObject frame,string actor)
    {
        var nation=frame["nations"]?.AsArray().FirstOrDefault(n=>n?["id"]?.ToString()==actor);
        if(nation==null)return null;
        var flow=nation["resource_flow"];
        var changes=Amounts(flow?["net"]);
        var stocks=(nation["stocks"]?.AsArray()??new JsonArray()).Select(s=>new LedgerStock(
            s!["id"]!.ToString(),s["name"]?.ToString()??s["id"]!.ToString(),Number(s["amount"]),
            changes.GetValueOrDefault(s["id"]!.ToString()))).ToArray();
        var energy=nation["energy"];
        return new(actor,nation["name"]!.ToString(),frame["period"]!.GetValue<int>(),flow?["period"]?.GetValue<int>()??-1,stocks) {
            Power=energy?["capacity"]!=null&&energy["demand"]!=null?new LedgerPower(Number(energy["capacity"]),
                energy["generated"]==null?null:Number(energy["generated"]),Number(energy["demand"]),
                energy["previous_short"]?.GetValue<bool>(),(int)Number(energy["fuel_short_plants"])):null };
    }
    public static Dictionary<string,double> Amounts(JsonNode? pairs)=>(pairs?.AsArray()??new JsonArray())
        .ToDictionary(p=>p![0]!.ToString(),p=>Number(p![1]));
    public static double Number(JsonNode? node)=>double.TryParse(node?.ToString(),NumberStyles.Float,CultureInfo.InvariantCulture,out double value)?value:0;
    public static string Quantity(double value)=>value.ToString("G6",CultureInfo.InvariantCulture);
    public static string Signed(double value)=>value>0?"+"+Quantity(value):value<0?Quantity(value):"—";
}
