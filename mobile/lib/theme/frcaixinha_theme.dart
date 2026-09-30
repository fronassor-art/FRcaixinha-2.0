import 'package:flutter/material.dart';

/// Cores da identidade FRcaixinha, usadas sem depender de dados financeiros.
abstract final class FRColors {
  static const background = Color(0xFF071F1B);
  static const surface = Color(0xFF10362F);
  static const elevated = Color(0xFF19483E);
  static const emerald = Color(0xFF4FC394);
  static const gold = Color(0xFFD8B977);
  static const text = Color(0xFFF3F7F2);
  static const muted = Color(0xFFB6CCC3);
}

abstract final class FRTheme {
  static ThemeData get dark {
    final scheme = ColorScheme.fromSeed(
      seedColor: FRColors.emerald,
      brightness: Brightness.dark,
      surface: FRColors.background,
    ).copyWith(
      primary: FRColors.emerald,
      onPrimary: FRColors.background,
      secondary: FRColors.gold,
      onSecondary: FRColors.background,
      surface: FRColors.background,
      onSurface: FRColors.text,
      surfaceContainerLow: FRColors.surface,
      surfaceContainer: FRColors.surface,
      surfaceContainerHigh: FRColors.elevated,
      outline: FRColors.muted,
    );

    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorScheme: scheme,
      scaffoldBackgroundColor: FRColors.background,
      appBarTheme: const AppBarTheme(
        backgroundColor: FRColors.background,
        foregroundColor: FRColors.text,
        centerTitle: false,
      ),
      cardTheme: CardThemeData(
        color: FRColors.surface,
        elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: FRColors.surface,
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(16)),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 18,
          vertical: 16,
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(0, 52),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
          ),
          textStyle: const TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: FRColors.surface,
        indicatorColor: FRColors.elevated,
        labelTextStyle: WidgetStateProperty.all(
          const TextStyle(fontWeight: FontWeight.w600),
        ),
      ),
      textTheme: const TextTheme(
        headlineLarge: TextStyle(fontSize: 32, fontWeight: FontWeight.w700),
        headlineMedium: TextStyle(fontSize: 26, fontWeight: FontWeight.w700),
        titleLarge: TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
        titleMedium: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        bodyMedium: TextStyle(fontSize: 14, height: 1.4),
      ).apply(bodyColor: FRColors.text, displayColor: FRColors.text),
    );
  }
}
