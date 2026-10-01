import 'package:flutter/material.dart';

import '../theme/frcaixinha_theme.dart';

/// Monograma tipográfico leve, inspirado na referência oficial.
class FRBrandMark extends StatelessWidget {
  const FRBrandMark({super.key, this.size = 72});

  final double size;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'FRcaixinha',
      image: true,
      child: ExcludeSemantics(
        child: Container(
          width: size,
          height: size,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: FRColors.surface,
            borderRadius: BorderRadius.circular(size * 0.25),
            border: Border.all(color: FRColors.gold.withValues(alpha: 0.45)),
          ),
          child: Text(
            'FR',
            style: TextStyle(
              color: FRColors.gold,
              fontSize: size * 0.40,
              fontWeight: FontWeight.w800,
              letterSpacing: -size * 0.035,
            ),
          ),
        ),
      ),
    );
  }
}
