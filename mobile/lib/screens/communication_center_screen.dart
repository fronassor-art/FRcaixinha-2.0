import 'package:flutter/material.dart';
import '../services/api_client.dart';
import '../services/session.dart';

class CommunicationCenterScreen extends StatefulWidget {
  const CommunicationCenterScreen({super.key});
  @override State<CommunicationCenterScreen> createState() => _CommunicationCenterScreenState();
}
class _CommunicationCenterScreenState extends State<CommunicationCenterScreen> {
  final api = ApiClient();
  Map<String,dynamic>? prefs, summary; bool loading=true; String? error;
  bool inApp=true,email=false,payment=true,loan=true,collection=true,account=true;
  @override void initState(){super.initState();load();}
  Future<void> load() async { api.token=await Session.getToken(); try { final p=await api.get('/communications/preferences'); final s=await api.get('/communications/summary');
    if(mounted)setState((){prefs=p;summary=s;inApp=p['in_app_enabled']??true;email=p['email_enabled']??false;payment=p['payment_alerts']??true;loan=p['loan_alerts']??true;collection=p['collection_alerts']??true;account=p['account_alerts']??true;loading=false;error=null;});
  } catch(e){if(mounted)setState(() { loading=false; error=e.toString(); });}}
  Future<void> save() async { try { await api.put('/communications/preferences', {'in_app_enabled':inApp,'email_enabled':email,'payment_alerts':payment,'loan_alerts':loan,'collection_alerts':collection,'account_alerts':account}); if(mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content:Text('Preferências salvas.'))); } catch(e){if(mounted)ScaffoldMessenger.of(context).showSnackBar(SnackBar(content:Text(e.toString())));} }
  @override Widget build(BuildContext context)=>Scaffold(appBar:AppBar(title:const Text('Central de Comunicação')),body:loading?const Center(child:CircularProgressIndicator()):error!=null?Center(child:Text(error!)):ListView(padding:const EdgeInsets.all(16),children:[if(summary!=null)Card(child:ListTile(title:Text('Não lidas: ${summary!['unread']}'),subtitle:Text('Total: ${summary!['total']}'))),const SizedBox(height:8),const Text('Canais',style:TextStyle(fontSize:18,fontWeight:FontWeight.bold)),SwitchListTile(title:const Text('Notificações no aplicativo'),value:inApp,onChanged:(v)=>setState(()=>inApp=v)),SwitchListTile(title:const Text('Receber por e-mail'),subtitle:const Text('O e-mail precisa estar configurado no sistema.'),value:email,onChanged:(v)=>setState(()=>email=v)),const Divider(),const Text('Tipos de aviso',style:TextStyle(fontSize:18,fontWeight:FontWeight.bold)),SwitchListTile(title:const Text('Pagamentos e contribuições'),value:payment,onChanged:(v)=>setState(()=>payment=v)),SwitchListTile(title:const Text('Empréstimos e parcelas'),value:loan,onChanged:(v)=>setState(()=>loan=v)),SwitchListTile(title:const Text('Cobrança e inadimplência'),value:collection,onChanged:(v)=>setState(()=>collection=v)),SwitchListTile(title:const Text('Conta e segurança'),value:account,onChanged:(v)=>setState(()=>account=v)),const SizedBox(height:12),FilledButton.icon(onPressed:save,icon:const Icon(Icons.save),label:const Text('Salvar preferências'))]));
}
