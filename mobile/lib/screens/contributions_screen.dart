import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../app.dart';
import '../models/finance_models.dart';
import '../repositories/app_repository.dart';
import 'payments/pix_payment_screen.dart';

class ContributionsScreen extends StatefulWidget { final AppRepository? repository; const ContributionsScreen({super.key,this.repository}); @override State<ContributionsScreen> createState()=>_ContributionsScreenState(); }
class _ContributionsScreenState extends State<ContributionsScreen> {
  List<ContributionItem> items=[]; List<FinancialObligation> obligations=[]; Map<int,FinancialObligation> contributionObligations={}; Map<String,dynamic>? summary; bool loading=true; String? error;
  @override void initState(){super.initState(); load();}
  AppRepository get repository=>widget.repository??context.read<AppState>().repository;
  Future<void> load() async { setState(()=>loading=true); try { final a=await repository.contributions(); final b=await repository.contributionSummary(); final o=await repository.financialObligations(); final rows=(a['items'] as List).map((e)=>ContributionItem.fromJson(Map<String,dynamic>.from(e))).toList(); final links=<int,FinancialObligation>{for(final item in o) if(item.type==FinancialObligationType.contribution&&rows.any((c)=>c.id==item.obligationId)) item.obligationId:item}; if(mounted)setState(() { items=rows; summary=b; obligations=o; contributionObligations=links; error=null; }); } catch(e){if(mounted)setState(()=>error=e.toString());} finally{if(mounted)setState(()=>loading=false);} }
  Future<void> pay(ContributionItem contribution) async { final obligation=contributionObligations[contribution.id]; try { final payment=await repository.createContributionPix(contribution.id); if(!mounted)return; await Navigator.push(context,MaterialPageRoute(builder:(_)=>PixPaymentScreen(repository:repository,payment:payment,receiptAvailable:obligation?.receiptAvailable??false,onPaymentConfirmed:(){load();}))); if(mounted)await load(); } catch(e){ if(mounted)ScaffoldMessenger.of(context).showSnackBar(SnackBar(content:Text(e.toString()))); } }
  String statusLabel(FinancialObligationStatus status)=>switch(status){FinancialObligationStatus.pending=>'Pendente',FinancialObligationStatus.partial=>'Parcial',FinancialObligationStatus.overdue=>'Em atraso',FinancialObligationStatus.paid=>'Quitado'};
  @override Widget build(BuildContext context){ return Scaffold(appBar:AppBar(title:const Text('Contribuições')),body:RefreshIndicator(onRefresh:load,child:ListView(padding:const EdgeInsets.all(16),children:[
    if(summary!=null) Card(child:Padding(padding:const EdgeInsets.all(16),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[Text('Total pago: R\$ ${summary!['paid_total']}'),Text('Pendente: R\$ ${summary!['pending_total']}'),Text('Plano: R\$ ${summary!['expected_total']}')]))) ,
    if(error!=null) Text(error!), if(loading) const Center(child:CircularProgressIndicator()),
    ...items.map((c){final o=contributionObligations[c.id];final status=o?.financialStatus??financialObligationStatusFromJson(c.status);final due=o?.dueDate??c.dueDate;return Card(child:ListTile(title:Text('Competência ${c.competence.substring(0,7)}'),subtitle:Text('Valor original: R\$ ${o?.amountDue??c.amount}\nValor pago: R\$ ${o?.amountPaid??c.paidAmount}\nSaldo pendente: R\$ ${o?.outstandingAmount??c.amount}\nVencimento: ${due?.toIso8601String().substring(0,10)??'-'}\nEstado: ${statusLabel(status)}${o!=null&&o.daysOverdue>0?' • ${o.daysOverdue} dias em atraso':''}'),isThreeLine:true,trailing:status==FinancialObligationStatus.paid?const Icon(Icons.check_circle):ElevatedButton(onPressed:()=>pay(c),child:const Text('Pagar Pix'))));})
  ]))); }
}
