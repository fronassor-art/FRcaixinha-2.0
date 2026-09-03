import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'repositories/app_repository.dart';
import 'services/api_client.dart';
import 'services/session.dart';
import 'screens/login_screen.dart';
import 'screens/home_screen.dart';
import 'screens/contributions_screen.dart';
import 'screens/notifications_screen.dart';
import 'screens/communication_center_screen.dart';
import 'screens/privacy_center_screen.dart';
import 'screens/member_portal_screen.dart';
import 'screens/profile_screen.dart';
import 'screens/statement_screen.dart';
import 'screens/loans/loans_screen.dart';
import 'screens/loans/loan_request_screen.dart';
import 'screens/loans/loan_detail_screen.dart';
import 'screens/admin/admin_dashboard_screen.dart';
import 'screens/admin/operations_dashboard_screen.dart';
import 'screens/admin/collections_dashboard_screen.dart';
import 'screens/admin/governance_dashboard_screen.dart';
import 'screens/admin/reports_dashboard_screen.dart';
import 'screens/admin/executive_dashboard_screen.dart';
import 'screens/admin/financial_projection_screen.dart';
import 'screens/admin/capacity_optimizer_screen.dart';
import 'screens/admin/resource_allocation_screen.dart';
import 'screens/admin/operational_control_screen.dart';
import 'screens/admin/workflow_compliance_screen.dart';
import 'screens/admin/executive_risk_response_screen.dart';
import 'screens/admin/executive_risk_governance_screen.dart';
import 'screens/admin/executive_risk_decision_screen.dart';
import 'screens/admin/executive_risk_execution_screen.dart';
import 'screens/admin/continuous_improvement_dashboard_screen.dart';
import 'screens/admin/continuous_improvement_balancing_screen.dart';
import 'screens/admin/continuous_improvement_finalization_screen.dart';

class AppState extends ChangeNotifier {
  final ApiClient api = ApiClient();
  late final AppRepository repository = AppRepository(api);
  bool initialized = false;
  bool authenticated = false;
  String? role;

  Future<void> initialize() async {
    final token = await Session.getToken();
    api.token = token;
    authenticated = token != null && token.isNotEmpty;
    if (authenticated) {
      try {
        final profile = await repository.profile();
        role = profile['role'] as String?;
      } catch (_) {
        await Session.clear();
        api.token = null;
        authenticated = false;
      }
    }
    initialized = true;
    notifyListeners();
  }

  Future<void> login(String token) async {
    await Session.saveToken(token);
    api.token = token;
    authenticated = true;
    final profile = await repository.profile();
    role = profile['role'] as String?;
    notifyListeners();
  }

  Future<void> logout() async {
    await Session.clear();
    api.token = null;
    authenticated = false;
    role = null;
    notifyListeners();
  }
}

GoRouter createRouter(AppState state) => GoRouter(
  refreshListenable: state,
  redirect: (context, route) {
    if (!state.initialized) return null;
    final loggedIn = state.authenticated;
    final isLogin = route.matchedLocation == '/login';
    if (!loggedIn && !isLogin) return '/login';
    if (loggedIn && isLogin) return '/';
    return null;
  },
  routes: [
    GoRoute(path: '/login', builder: (_, __) => const LoginScreen()),
    GoRoute(path: '/', builder: (_, __) => const HomeScreen()),
    GoRoute(path: '/contributions', builder: (_, __) => const ContributionsScreen()),
    GoRoute(path: '/notifications', builder: (_, __) => const NotificationsScreen()),
    GoRoute(path: '/communications', builder: (_, __) => const CommunicationCenterScreen()),
    GoRoute(path: '/privacy', builder: (_, __) => const PrivacyCenterScreen()),
    GoRoute(path: '/profile', builder: (_, __) => const ProfileScreen()),
    GoRoute(path: '/statement', builder: (_, __) => const StatementScreen()),
    GoRoute(path: '/member-portal', builder: (_, __) => const MemberPortalScreen()),
    GoRoute(path: '/loans', builder: (_, __) => const LoansScreen()),
    GoRoute(path: '/loans/request', builder: (_, __) => const LoanRequestScreen()),
    GoRoute(path: '/loans/:id', builder: (_, state) => LoanDetailScreen(loanId: int.parse(state.pathParameters['id']!))),
    GoRoute(path: '/admin', builder: (_, __) => const AdminDashboardScreen()),
    GoRoute(path: '/admin/operations', builder: (_, __) => const OperationsDashboardScreen()),
    GoRoute(path: '/admin/collections', builder: (_, __) => const CollectionsDashboardScreen()),
    GoRoute(path: '/admin/governance', builder: (_, __) => const GovernanceDashboardScreen()),
    GoRoute(path: '/admin/reports', builder: (_, __) => const ReportsDashboardScreen()),
    GoRoute(path: '/admin/executive-dashboard', builder: (_, __) => const ExecutiveDashboardScreen()),
    GoRoute(path: '/admin/financial-projection', builder: (_, __) => const FinancialProjectionScreen()),
    GoRoute(path: '/admin/capacity-optimizer', builder: (_, __) => const CapacityOptimizerScreen()),
    GoRoute(path: '/admin/resource-allocation', builder: (_, __) => const ResourceAllocationScreen()),
    GoRoute(path: '/admin/operational-control', builder: (_, __) => const OperationalControlScreen()),
    GoRoute(path: '/admin/workflow-compliance', builder: (_, __) => const WorkflowComplianceScreen()),
    GoRoute(path: '/admin/executive-risk-response', builder: (_, __) => const ExecutiveRiskResponseScreen()),
    GoRoute(path: '/admin/executive-risk-decisions', builder: (_, __) => const ExecutiveRiskDecisionScreen()),
    GoRoute(path: '/admin/executive-risk-governance', builder: (_, __) => const ExecutiveRiskGovernanceScreen()),
    GoRoute(path: '/admin/executive-risk-execution', builder: (_, __) => const ExecutiveRiskExecutionScreen()),
    GoRoute(path: '/admin/continuous-improvement-dashboard', builder: (_, __) => const ContinuousImprovementDashboardScreen()),
    GoRoute(path: '/admin/continuous-improvement-balancing', builder: (_, __) => const ContinuousImprovementBalancingScreen()),
    GoRoute(path: '/admin/continuous-improvement-finalization', builder: (_, __) => const ContinuousImprovementFinalizationScreen()),
  ],
);
