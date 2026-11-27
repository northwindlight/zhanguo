module {
  func.func @main(%input: !torch.vtensor<[1,?,192],f32>) -> !torch.vtensor<[1,?,192],f32>
      attributes {torch.assume_strict_symbolic_shapes} {
    %width = torch.constant.int 192
    %dims = torch.prim.ListConstruct %width : (!torch.int) -> !torch.list<int>
    %none = torch.constant.none
    %epsilon = torch.constant.float 1.000000e-05
    %cudnn = torch.constant.bool true
    %output = torch.aten.layer_norm %input, %dims, %none, %none, %epsilon, %cudnn
        : !torch.vtensor<[1,?,192],f32>, !torch.list<int>, !torch.none, !torch.none, !torch.float, !torch.bool
          -> !torch.vtensor<[1,?,192],f32>
    return %output : !torch.vtensor<[1,?,192],f32>
  }
}
