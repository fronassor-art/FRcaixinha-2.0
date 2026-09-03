import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:go_router/go_router.dart';
import '../app.dart';
import '../services/api_client.dart';

class LoginScreen extends StatefulWidget { const LoginScreen({super.key}); @override State<LoginScreen> createState()=>_LoginScreenState(); }
class _LoginScreenState extends State<LoginScreen>{
  final email=TextEditingController(); final password=TextEditingController(); bool loading=false; String? error;
  Future<void> submit() async {
    setState(()=>loading=true);
    try {
      final api=ApiClient();
      final r=await api.post('/auth/login', {'email':email.text.trim(),'password':password.text});
      await context.read<AppState>().login(r['access_token'] as String);
      if(mounted) context.go('/');
    } catch(e){ if(mounted)setState(()=>error=e.toString()); }
    if(mounted)setState(()=>loading=false);
  }
  @override Widget build(BuildContext context)=>Scaffold(body:Center(child:SingleChildScrollView(padding:const EdgeInsets.all(24),child:ConstrainedBox(constraints:const BoxConstraints(maxWidth:420),child:Column(children:[
    const Icon(Icons.account_balance_wallet_outlined,size:64), const SizedBox(height:16), const Text('FRcaixinha',style:TextStyle(fontSize:28,fontWeight:FontWeight.bold)), const SizedBox(height:28),
    TextField(controller:email,keyboardType:TextInputType.emailAddress,decoration:const InputDecoration(labelText:'E-mail',border:OutlineInputBorder())), const SizedBox(height:12),
    TextField(controller:password,obscureText:true,decoration:const InputDecoration(labelText:'Senha',border:OutlineInputBorder())),
    if(error!=null) Padding(padding:const EdgeInsets.only(top:12),child:Text(error!,style:TextStyle(color:Theme.of(context).colorScheme.error))),
    const SizedBox(height:20), SizedBox(width:double.infinity,child:FilledButton(onPressed:loading?null:submit,child:Text(loading?'Entrando...':'Entrar'))),
  ]))));
  @override void dispose(){email.dispose();password.dispose();super.dispose();}
}
