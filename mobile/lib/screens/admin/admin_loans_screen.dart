import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class AdminLoansScreen extends StatefulWidget { const AdminLoansScreen({super.key}); @override State<AdminLoansScreen> createState()=>_AdminLoansScreenState(); }
class _AdminLoansScreenState extends State<AdminLoansScreen>{
  final api=ApiClient(); List<dynamic> items=[]; String? error;
  @override void initState(){super.initState();load();}
  Future<void> load() async { api.token = await Session.getToken(); try{final r=await api.get('/admin/loans?status=REQUESTED');items=r['items']??[];if(mounted)setState(()=>error=null);}catch(e){if(mounted)setState(()=>error=e.toString());} if(mounted)setState((){}); }
  Future<void> decide(int id,bool approve,{bool forceException=false,String? adminNote}) async {
    try {
      await api.post('/loans/$id/decision',{
        'approve':approve,
        'force_exception':forceException,
        if(adminNote != null) 'admin_note':adminNote,
      });
      await load();
    } catch(e) {
      final message=e.toString();
      if(approve && !forceException &&
          (message.contains('FINANCIAL_APPROVAL_REVIEW') ||
           message.contains('FINANCIAL_APPROVAL_BLOCKED'))) {
        await _requestExceptionJustification(id);
        return;
      }
      if(mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content:Text(message)),
        );
      }
    }
  }

  Future<void> _requestExceptionJustification(int id) async {
    final controller=TextEditingController();

    final note=await showDialog<String>(
      context:context,
      builder:(context)=>AlertDialog(
        title:const Text('Crédito Especial'),
        content:Column(
          mainAxisSize:MainAxisSize.min,
          crossAxisAlignment:CrossAxisAlignment.start,
          children:[
            const Text(
              'Esta solicitação exige uma exceção administrativa.',
            ),
            const SizedBox(height:16),
            TextField(
              controller:controller,
              minLines:3,
              maxLines:4,
              decoration:const InputDecoration(
                labelText:'Justificativa obrigatória',
                hintText:'Informe o motivo da aprovação excepcional.',
                border:OutlineInputBorder(),
              ),
            ),
          ],
        ),
        actions:[
          TextButton(
            onPressed:()=>Navigator.pop(context),
            child:const Text('Cancelar'),
          ),
          FilledButton(
            onPressed:(){
              final value=controller.text.trim();
              if(value.length<5){
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content:Text('Informe uma justificativa com pelo menos 5 caracteres.'),
                  ),
                );
                return;
              }
              Navigator.pop(context,value);
            },
            child:const Text('Aprovar exceção'),
          ),
        ],
      ),
    );

    controller.dispose();

    if(note == null || note.trim().length<5) return;

    await decide(
      id,
      true,
      forceException:true,
      adminNote:note.trim(),
    );
  }
  @override Widget build(BuildContext context)=>Scaffold(appBar:AppBar(title:const Text('Solicitações de empréstimo')),body:RefreshIndicator(onRefresh:load,child:ListView(padding:const EdgeInsets.all(12),children:[if(error!=null)Text(error!),if(error==null && items.isEmpty)const Card(child:Padding(padding:EdgeInsets.all(20),child:Center(child:Text('Nenhuma solicitação de empréstimo pendente.')))),...items.map((x)=>Card(child:ListTile(title:Text(x['member_name']??'Membro'),subtitle:Text('R\$ ${x['principal']} • ${x['installments']} parcelas • taxa ${x['monthly_rate']}'),trailing:Wrap(spacing:4,children:[IconButton(tooltip:'Rejeitar',onPressed:()=>decide(x['id'],false),icon:const Icon(Icons.close)),IconButton(tooltip:'Aprovar',onPressed:()=>decide(x['id'],true),icon:const Icon(Icons.check))]))))])));
}
