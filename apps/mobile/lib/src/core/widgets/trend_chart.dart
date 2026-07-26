import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../model/figures.dart';
import '../money/money.dart';
import '../money/money_format.dart';

/// A 30-day trade trend.
///
/// Drawn from a series that already contains a zero for every quiet day, so the
/// line dips where the shop was shut instead of sloping smoothly across the gap
/// and implying trade that never happened.
class TrendChart extends StatelessWidget {
  const TrendChart({
    super.key,
    required this.points,
    required this.currency,
    this.height = 150,
  });

  final List<TrendPoint> points;
  final String currency;
  final double height;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    if (points.length < 2) {
      return SizedBox(
        height: height,
        child: Center(
          child: Text(
            'Not enough history yet',
            style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
          ),
        ),
      );
    }

    final double maxValue = points
        .map((TrendPoint p) => p.value)
        .fold<double>(0, (double a, double b) => a > b ? a : b);
    final Color line = theme.colorScheme.primary;

    return SizedBox(
      height: height,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(8, 16, 16, 4),
        child: LineChart(
          LineChartData(
            minY: 0,
            // A flat-zero series would collapse the axis and draw the line on
            // the border; a nominal ceiling keeps the empty state readable.
            maxY: maxValue <= 0 ? 1 : maxValue * 1.15,
            gridData: FlGridData(
              show: true,
              drawVerticalLine: false,
              horizontalInterval: maxValue <= 0 ? 1 : maxValue / 2,
              getDrawingHorizontalLine: (double value) => FlLine(
                color: theme.colorScheme.outlineVariant.withOpacity(0.5),
                strokeWidth: 1,
                dashArray: <int>[4, 4],
              ),
            ),
            borderData: FlBorderData(show: false),
            titlesData: FlTitlesData(
              topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
              rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
              leftTitles: AxisTitles(
                sideTitles: SideTitles(
                  showTitles: true,
                  reservedSize: 46,
                  interval: maxValue <= 0 ? 1 : maxValue,
                  getTitlesWidget: (double value, TitleMeta meta) {
                    if (value <= 0) return const SizedBox.shrink();
                    return Text(
                      MoneyFormat.compact(
                        Money.fromJson(<String, Object?>{
                          'amount': value.toStringAsFixed(0),
                          'side': 'debit',
                          'currency': currency,
                        }),
                      ),
                      style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
                    );
                  },
                ),
              ),
              bottomTitles: AxisTitles(
                sideTitles: SideTitles(
                  showTitles: true,
                  reservedSize: 22,
                  interval: (points.length / 4).ceilToDouble(),
                  getTitlesWidget: (double value, TitleMeta meta) {
                    final int index = value.round();
                    if (index < 0 || index >= points.length) {
                      return const SizedBox.shrink();
                    }
                    final DateTime date = points[index].date;
                    return Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(
                        '${date.day}/${date.month}',
                        style: theme.textTheme.labelSmall
                            ?.copyWith(color: context.mutedColor),
                      ),
                    );
                  },
                ),
              ),
            ),
            lineTouchData: LineTouchData(
              touchTooltipData: LineTouchTooltipData(
                getTooltipColor: (LineBarSpot spot) => theme.colorScheme.inverseSurface,
                getTooltipItems: (List<LineBarSpot> spots) => spots
                    .map(
                      (LineBarSpot spot) => LineTooltipItem(
                        '${_label(points[spot.x.round()].date)}\n'
                        '${MoneyFormat.compact(Money.fromJson(<String, Object?>{
                              'amount': spot.y.toStringAsFixed(2),
                              'side': 'debit',
                              'currency': currency,
                            }))}',
                        TextStyle(
                          color: theme.colorScheme.onInverseSurface,
                          fontWeight: FontWeight.w600,
                          fontSize: 12,
                        ),
                      ),
                    )
                    .toList(),
              ),
            ),
            lineBarsData: <LineChartBarData>[
              LineChartBarData(
                spots: <FlSpot>[
                  for (int i = 0; i < points.length; i++)
                    FlSpot(i.toDouble(), points[i].value),
                ],
                isCurved: true,
                curveSmoothness: 0.22,
                preventCurveOverShooting: true,
                color: line,
                barWidth: 2.4,
                dotData: const FlDotData(show: false),
                belowBarData: BarAreaData(
                  show: true,
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: <Color>[line.withOpacity(0.22), line.withOpacity(0.01)],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  static String _label(DateTime date) => '${date.day}/${date.month}/${date.year}';
}
