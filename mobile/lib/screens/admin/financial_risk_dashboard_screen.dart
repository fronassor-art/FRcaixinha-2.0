import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class FinancialRiskDashboardScreen extends StatefulWidget {
  const FinancialRiskDashboardScreen({super.key});
  @override State<FinancialRiskDashboardScreen> createState()=>_FinancialRiskDashboardScreenState();
}
class _FinancialRiskDashboardScreenState extends State<FinancialRiskDashboardScreen>{
 final api=ApiClient(); Map<String,dynamic>? data; String? error;
 @override void initState(){super.initState();load();}
 Future<void> load() async {api.token=await Session.getToken(); try{data=await api.get('/admin/financial-risk/summary'); if(mounted)setState(()=>error=null);}catch(e){if(mounted)setState(()=>error=e.toString());} if(mounted)setState((){});}
 @override Widget build(BuildContext context){final d=data; return Scaffold(appBar:AppBar(title:const Text('Risco financeiro e antifraude'),actions:[IconButton(onPressed:load,icon:const Icon(Icons.refresh))]),body:RefreshIndicator(onRefresh:load,child:ListView(padding:const EdgeInsets.all(16),children:[if(error!=null)Text(error!),if(d==null&&error==null)const Padding(padding:EdgeInsets.all(40),child:Center(child:CircularProgressIndicator())),if(d!=null)...[_card('Total de avaliações','${d['total']}',Icons.analytics_outlined),_card('PASS','${d['pass']}',Icons.check_circle_outline),_card('REVIEW','${d['review']}',Icons.rate_review_outlined),_card('BLOCKED','${d['blocked']}',Icons.block_outlined),_card('Maior score','${d['max_score']}',Icons.warning_amber_outlined),const SizedBox(height:12),const Text('O score é explicável e serve como apoio à decisão; não substitui análise administrativa, contratual ou jurídica.',style:TextStyle(fontSize:13))]]));}
 Widget _card(String t,String v,IconData i)=>Card(child:ListTile(leading:Icon(i),title:Text(t),trailing:Text(v,style:const TextStyle(fontWeight:FontWeight.bold,fontSize:18))));
}
