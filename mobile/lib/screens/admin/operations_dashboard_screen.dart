import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class OperationsDashboardScreen extends StatefulWidget {
  const OperationsDashboardScreen({super.key});
  @override State<OperationsDashboardScreen> createState() => _OperationsDashboardScreenState();
}

class _OperationsDashboardScreenState extends State<OperationsDashboardScreen> {
  final api = ApiClient();
  Map<String,dynamic>? data;
  String? error;
  bool loading = false;

  @override void initState(){super.initState(); load();}
  Future<void> load() async {
    setState(()=>loading=true); api.token=await Session.getToken();
    try { data=await api.get('/admin/operations/dashboard'); error=null; }
    catch(e){error=e.toString();} finally {if(mounted)setState(()=>loading=false);}
  }
  @override Widget build(BuildContext context){
    final d=data; final rec=d?['reconciliation'];
    return Scaffold(appBar: AppBar(title: const Text('Operação e Reconciliação')),
      body: RefreshIndicator(onRefresh: load, child: ListView(padding: const EdgeInsets.all(16), children:[
        if(error!=null) Text(error!),
        if(loading && d==null) const Center(child:CircularProgressIndicator()),
        if(d!=null)...[
          Card(child: ListTile(leading: Icon(d['go_no_go']=='GO'?Icons.check_circle:Icons.warning_amber), title: Text('Status: ${d['go_no_go']}'), subtitle: Text('Reconciliação: ${rec['status']}'))),
          _tile('Saldo do Ledger', 'R$ ${d['ledger']['balance']}'),
          _tile('Pagamentos aprovados', 'R$ ${d['payments']['approved']}'),
          _tile('Ainda não lançados', 'R$ ${d['payments']['unposted_amount']}'),
          _tile('Webhooks pendentes', '${d['webhooks']['pending']}'),
          _tile('Parcelas abertas', '${d['installments']['open']}'),
          const SizedBox(height: 12),
          const Text('Reconciliação', style: TextStyle(fontSize:18,fontWeight:FontWeight.bold)),
          ...((rec['findings'] as List).map((x)=>ListTile(dense:true, leading:Icon(x['status']=='PASS'?Icons.check:Icons.error), title:Text(x['code']), subtitle:Text(x['details'])))),
        ]
      ])));
  }
  Widget _tile(String a,String b)=>Card(child:ListTile(title:Text(a),trailing:Text(b,style:const TextStyle(fontWeight:FontWeight.bold))));
}
