import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class AdminMembersScreen extends StatefulWidget { const AdminMembersScreen({super.key}); @override State<AdminMembersScreen> createState()=>_AdminMembersScreenState(); }
class _AdminMembersScreenState extends State<AdminMembersScreen>{ final api=ApiClient(); List<dynamic> items=[]; String? error;
 @override void initState(){super.initState();load();}
 Future<void> load() async { api.token = await Session.getToken();try{final r=await api.get('/admin/members');items=r['items']??[];if(mounted)setState(()=>error=null);}catch(e){if(mounted)setState(()=>error=e.toString());}if(mounted)setState((){});}
 @override Widget build(BuildContext context)=>Scaffold(appBar:AppBar(title:const Text('Participantes')),body:RefreshIndicator(onRefresh:load,child:ListView(padding:const EdgeInsets.all(12),children:[if(error!=null)Text(error!),...items.map((x)=>Card(child:ListTile(leading:const CircleAvatar(child:Icon(Icons.person)),title:Text(x['user']['name']??''),subtitle:Text('${x['user']['email']??''}\nStatus: ${x['status']} • Cotas: ${x['quota_units']}'),isThreeLine:true)))])));
}
