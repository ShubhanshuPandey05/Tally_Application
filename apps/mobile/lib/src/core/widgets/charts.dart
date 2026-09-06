import 'dart:math' as math;

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../money/money_format.dart';

/// The drawing kit the dashboard is built from.
///
/// The brief is "Google Analytics for Tally", and an owner reads a shape faster
/// than a column of rupees: whether the line is climbing, how much of the debt
/// is old, which three customers are most of the month. So every figure that
/// has a denominator behind it gets drawn against that denominator, and the
/// exact number stays beside the picture rather than being replaced by it.
///
/// Nothing in here invents a colour. The palette is [AppTheme]'s tile set and
/// the three semantic colours, because a chart that introduces its own hues
/// stops matching the tile above it and the app turns into a paint chart.

/// A bare trend line, no axes, sized to sit inside a metric tile.
///
/// Deliberately unlabelled. It answers "which way is this going?" in the space
/// a caption would take, and the figure it belongs to is already printed above
/// it -- so axis furniture here would be decoration paid for in pixels.
class Sparkline extends StatelessWidget {
  const Sparkline({
    super.key,
    required this.values,
    this.colour,
    this.height = 22,
    this.filled = true,
  });

  final List<double> values;
  final Color? colour;
  final double height;
  final bool filled;

  @override
  Widget build(BuildContext context) {
    // One point is not a trend. Drawing a dot, or a flat line through a single
    // reading, would claim a shape the data does not have.
    if (values.length < 2) return SizedBox(height: height);

    return SizedBox(
      height: height,
      width: double.infinity,
      child: CustomPaint(
        painter: _SparklinePainter(
          values: values,
          colour: colour ?? Theme.of(context).colorScheme.primary,
          filled: filled,
        ),
      ),
    );
  }
}

class _SparklinePainter extends CustomPainter {
  const _SparklinePainter({
    required this.values,
    required this.colour,
    required this.filled,
  });

  final List<double> values;
  final Color colour;
  final bool filled;

  @override
  void paint(Canvas canvas, Size size) {
    double lowest = values.first;
    double highest = values.first;
    for (final double value in values) {
      lowest = math.min(lowest, value);
      highest = math.max(highest, value);
    }
    // A flat series has no span to scale against. Give it a nominal one so the
    // line lands mid-height instead of dividing by zero.
    final double span = (highest - lowest).abs() < 1e-9 ? 1 : highest - lowest;
    final double step = size.width / (values.length - 1);
    // Half the stroke at each edge, so the highest and lowest points are not
    // clipped by the box.
    final double usable = size.height - 3;

    final Path line = Path();
    for (int i = 0; i < values.length; i++) {
      final double x = i * step;
      final double y = size.height - 1.5 - ((values[i] - lowest) / span) * usable;
      if (i == 0) {
        line.moveTo(x, y);
      } else {
        line.lineTo(x, y);
      }
    }

    if (filled) {
      final Path area = Path.from(line)
        ..lineTo(size.width, size.height)
        ..lineTo(0, size.height)
        ..close();
      canvas.drawPath(
        area,
        Paint()
          ..shader = LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: <Color>[colour.withOpacity(0.26), colour.withOpacity(0)],
          ).createShader(Offset.zero & size),
      );
    }

    canvas.drawPath(
      line,
      Paint()
        ..color = colour
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.8
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round,
    );
  }

  @override
  bool shouldRepaint(_SparklinePainter old) =>
      old.colour != colour || old.filled != filled || !identical(old.values, values);
}

/// One named series on a [TrendChart] or in a [ChartLegend].
class ChartSeries {
  const ChartSeries({
    required this.label,
    required this.colour,
    required this.values,
  });

  final String label;
  final Color colour;

  /// One value per x position. Series on the same chart must be the same
  /// length -- a shorter one would silently shift against the dates.
  final List<double> values;
}

/// How a [TrendChart] draws itself.
///
/// Both shapes answer different questions and an owner switches between them
/// mid-thought: the line is for "is trade climbing?", the bars for "how did
/// Tuesday compare with Wednesday?".
enum TrendShape { line, bars }

/// The dashboard's main chart: one or two series over the same days.
///
/// Drawn from a series that already contains a zero for every quiet day, so the
/// line dips where the shop was shut instead of sloping smoothly across the gap
/// and implying trade that never happened.
class TrendChart extends StatelessWidget {
  const TrendChart({
    super.key,
    required this.series,
    required this.dates,
    required this.currency,
    this.shape = TrendShape.line,
    this.height = 146,
  });

  final List<ChartSeries> series;
  final List<DateTime> dates;
  final String currency;
  final TrendShape shape;
  final double height;

  double get _ceiling {
    double highest = 0;
    for (final ChartSeries s in series) {
      for (final double value in s.values) {
        highest = math.max(highest, value);
      }
    }
    return highest;
  }

  /// The bottom of the axis, never above zero.
  ///
  /// Trade series are all-positive and this is always 0 for them, which keeps
  /// their axis exactly as it was. A running ledger balance is not: it crosses
  /// into credit, and an axis floored at zero would draw the whole overdrawn
  /// stretch flat along the bottom as though the account had simply emptied.
  double get _floor {
    double lowest = 0;
    for (final ChartSeries s in series) {
      for (final double value in s.values) {
        lowest = math.min(lowest, value);
      }
    }
    return lowest;
  }

  /// A little air above the highest point and below the lowest, so the line
  /// never runs along the frame. A series that is entirely flat at zero gets a
  /// nominal ceiling rather than a collapsed axis.
  double get _top => _ceiling > 0 ? _ceiling * 1.18 : (_floor < 0 ? 0 : 1);

  double get _bottom => _floor < 0 ? _floor * 1.18 : 0;

  double get _step {
    final double span = _top - _bottom;
    return span <= 0 ? 1 : span / 2;
  }

  /// Whether an axis label at [value] would say anything.
  bool _skipLabel(double value) {
    if (value < _floor - 0.5 || value > _ceiling + 0.5) return true;
    // On an all-positive chart the axis line *is* the baseline and labelling it
    // "0" states nothing. On a signed one it is the line between debit and
    // credit, which is the most useful label on the chart.
    return _floor >= 0 && value <= 0;
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    if (dates.length < 2 || series.isEmpty) {
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

    return SizedBox(
      height: height,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(4, 10, 12, 2),
        child: shape == TrendShape.line ? _line(context) : _bars(context),
      ),
    );
  }

  // ---- shared axis furniture ----------------------------------------------

  /// Horizontal rules only, dashed and faint. Vertical ones would fence every
  /// day into a cell and turn a trend into a table.
  FlGridData _grid(BuildContext context) => FlGridData(
        show: true,
        drawVerticalLine: false,
        horizontalInterval: _step,
        getDrawingHorizontalLine: (double value) => FlLine(
          color: context.lineColor.withOpacity(0.7),
          strokeWidth: 1,
          dashArray: <int>[4, 4],
        ),
      );

  FlTitlesData _titles(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final TextStyle? style =
        theme.textTheme.labelSmall?.copyWith(color: context.mutedColor);

    return FlTitlesData(
      topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
      rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
      leftTitles: AxisTitles(
        sideTitles: SideTitles(
          showTitles: true,
          reservedSize: 46,
          // Three gridlines, three labels. More would crowd a phone-width
          // chart with numbers nobody reads individually.
          interval: _step,
          getTitlesWidget: (double value, TitleMeta meta) {
            // Nothing outside the data: the axis is padded past the highest and
            // lowest points so the line does not touch the frame, and labelling
            // that padding puts a figure on the chart that no gridline and no
            // reading corresponds to.
            if (_skipLabel(value)) return const SizedBox.shrink();
            return Text(MoneyFormat.compactValue(value, currency), style: style);
          },
        ),
      ),
      bottomTitles: AxisTitles(
        sideTitles: SideTitles(
          showTitles: true,
          reservedSize: 20,
          interval: (dates.length / 4).ceilToDouble(),
          getTitlesWidget: (double value, TitleMeta meta) {
            final int index = value.round();
            if (index < 0 || index >= dates.length) return const SizedBox.shrink();
            final DateTime date = dates[index];
            return Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('${date.day}/${date.month}', style: style),
            );
          },
        ),
      ),
    );
  }

  String _tooltip(int index) {
    final DateTime date = dates[index];
    final StringBuffer text = StringBuffer('${date.day}/${date.month}/${date.year}');
    for (final ChartSeries s in series) {
      text.write('\n${s.label}  ${MoneyFormat.compactValue(s.values[index], currency)}');
    }
    return text.toString();
  }

  // ---- shapes --------------------------------------------------------------

  Widget _line(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return LineChart(
      LineChartData(
        minY: _bottom,
        maxY: _top,
        gridData: _grid(context),
        borderData: FlBorderData(show: false),
        titlesData: _titles(context),
        lineTouchData: LineTouchData(
          touchTooltipData: LineTouchTooltipData(
            getTooltipColor: (LineBarSpot spot) => theme.colorScheme.inverseSurface,
            fitInsideHorizontally: true,
            // One tooltip for the day, listing every series, rather than one
            // box per line stacked on top of each other.
            getTooltipItems: (List<LineBarSpot> spots) => <LineTooltipItem?>[
              LineTooltipItem(
                _tooltip(spots.first.x.round()),
                TextStyle(
                  color: theme.colorScheme.onInverseSurface,
                  fontWeight: FontWeight.w600,
                  fontSize: 12,
                ),
              ),
              for (int i = 1; i < spots.length; i++) null,
            ],
          ),
        ),
        lineBarsData: <LineChartBarData>[
          for (final ChartSeries s in series)
            LineChartBarData(
              spots: <FlSpot>[
                for (int i = 0; i < s.values.length; i++)
                  FlSpot(i.toDouble(), s.values[i]),
              ],
              isCurved: true,
              curveSmoothness: 0.22,
              preventCurveOverShooting: true,
              color: s.colour,
              barWidth: 2.4,
              dotData: const FlDotData(show: false),
              // Only the first series is filled. Two overlapping washes make
              // the region where they cross a third colour that means nothing.
              belowBarData: BarAreaData(
                show: identical(s, series.first),
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: <Color>[
                    s.colour.withOpacity(0.22),
                    s.colour.withOpacity(0.01),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _bars(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    // Bars share the width with every other day, so they thin out as the
    // window lengthens rather than overlapping their neighbours.
    final double width = dates.length > 40
        ? 3
        : dates.length > 20
            ? 5
            : 9;

    return BarChart(
      BarChartData(
        minY: _bottom,
        maxY: _top,
        alignment: BarChartAlignment.spaceBetween,
        gridData: _grid(context),
        borderData: FlBorderData(show: false),
        titlesData: _titles(context),
        barTouchData: BarTouchData(
          touchTooltipData: BarTouchTooltipData(
            getTooltipColor: (BarChartGroupData group) =>
                theme.colorScheme.inverseSurface,
            fitInsideHorizontally: true,
            getTooltipItem: (
              BarChartGroupData group,
              int groupIndex,
              BarChartRodData rod,
              int rodIndex,
            ) =>
                BarTooltipItem(
              _tooltip(group.x),
              TextStyle(
                color: theme.colorScheme.onInverseSurface,
                fontWeight: FontWeight.w600,
                fontSize: 12,
              ),
            ),
          ),
        ),
        barGroups: <BarChartGroupData>[
          for (int i = 0; i < dates.length; i++)
            BarChartGroupData(
              x: i,
              barsSpace: 1.5,
              barRods: <BarChartRodData>[
                for (final ChartSeries s in series)
                  BarChartRodData(
                    toY: s.values[i],
                    color: s.colour,
                    width: series.length > 1 ? width * 0.62 : width,
                    borderRadius: BorderRadius.circular(2),
                  ),
              ],
            ),
        ],
      ),
    );
  }
}

/// The key for a multi-series chart. Dots and names, nothing else -- the values
/// are on the chart and in the strip below it.
class ChartLegend extends StatelessWidget {
  const ChartLegend({super.key, required this.series});

  final List<ChartSeries> series;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Wrap(
      spacing: 14,
      runSpacing: 4,
      children: <Widget>[
        for (final ChartSeries s in series)
          Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(color: s.colour, shape: BoxShape.circle),
              ),
              const SizedBox(width: 6),
              Text(
                s.label,
                style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
              ),
            ],
          ),
      ],
    );
  }
}

/// One wedge of a [DonutBreakdown].
class DonutSlice {
  const DonutSlice({
    required this.label,
    required this.value,
    required this.colour,
    required this.display,
  });

  final String label;

  /// The magnitude the wedge is sized by. Always positive; a negative share of
  /// a whole is not something a ring can draw.
  final double value;

  final Color colour;

  /// The formatted figure shown in the legend, so the ring never becomes the
  /// only place a number lives.
  final String display;
}

/// A composition, as a ring with its legend beside it.
///
/// Used where the question is "how is this total split?" -- ageing buckets,
/// cash against bank. A ring is read in one look where six rows of rupees make
/// the reader do the arithmetic, and the legend keeps the exact figures.
class DonutBreakdown extends StatelessWidget {
  const DonutBreakdown({
    super.key,
    required this.slices,
    required this.centreLabel,
    required this.centreValue,
    this.diameter = 108,
  });

  final List<DonutSlice> slices;

  /// What the hole in the middle says. The total the wedges add up to, because
  /// a ring without its total leaves every percentage unanchored.
  final String centreLabel;
  final String centreValue;

  final double diameter;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final List<DonutSlice> drawable =
        slices.where((DonutSlice slice) => slice.value > 0).toList();
    if (drawable.isEmpty) return const SizedBox.shrink();

    final double total =
        drawable.fold<double>(0, (double sum, DonutSlice slice) => sum + slice.value);

    return Row(
      children: <Widget>[
        SizedBox(
          width: diameter,
          height: diameter,
          child: Stack(
            alignment: Alignment.center,
            children: <Widget>[
              PieChart(
                PieChartData(
                  sections: <PieChartSectionData>[
                    for (final DonutSlice slice in drawable)
                      PieChartSectionData(
                        value: slice.value,
                        color: slice.colour,
                        radius: 13,
                        showTitle: false,
                      ),
                  ],
                  sectionsSpace: 2,
                  centerSpaceRadius: diameter / 2 - 15,
                  // Twelve o'clock, so the first wedge starts where the eye
                  // does rather than at three o'clock.
                  startDegreeOffset: -90,
                  borderData: FlBorderData(show: false),
                ),
              ),
              Column(
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Text(
                    centreValue,
                    maxLines: 1,
                    style: theme.textTheme.titleMedium?.merge(AppTheme.amount),
                  ),
                  Text(
                    centreLabel,
                    style:
                        theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              for (final DonutSlice slice in drawable)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 2),
                  child: Row(
                    children: <Widget>[
                      Container(
                        width: 8,
                        height: 8,
                        decoration:
                            BoxDecoration(color: slice.colour, shape: BoxShape.circle),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          slice.label,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.labelSmall
                              ?.copyWith(color: context.mutedColor),
                        ),
                      ),
                      const SizedBox(width: 6),
                      Text(
                        slice.display,
                        style: theme.textTheme.labelSmall?.merge(AppTheme.amount),
                      ),
                      SizedBox(
                        width: 36,
                        child: Text(
                          '${(slice.value / total * 100).round()}%',
                          textAlign: TextAlign.end,
                          style: theme.textTheme.labelSmall
                              ?.copyWith(color: context.mutedColor),
                        ),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

/// One entry in a ranked list, drawn against the leader.
///
/// The bar is what turns "top customers" from five amounts into a shape: the
/// reader sees at a glance whether the month rests on one buyer or spreads
/// across all five, which is the question behind the list.
class ShareRow extends StatelessWidget {
  const ShareRow({
    super.key,
    required this.rank,
    required this.title,
    required this.value,
    required this.fraction,
    this.subtitle,
    this.share,
    this.colour,
    this.onTap,
  });

  final int rank;
  final String title;

  /// The formatted amount, right-aligned against the other rows.
  final String value;

  /// How long the bar is, 0..1 -- measured against the largest row rather than
  /// against the total, so the smallest entry is still a bar and not a sliver.
  final double fraction;

  final String? subtitle;

  /// Share of the whole, when the whole is known. A different question from
  /// [fraction], and text rather than geometry because "38% of sales" is a
  /// fact where the bar is a comparison.
  final String? share;

  final Color? colour;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Color bar = colour ?? AppTheme.tileBlue;

    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 6, 16, 6),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                SizedBox(
                  width: 18,
                  child: Text(
                    '$rank',
                    style: theme.textTheme.labelSmall?.copyWith(
                      color: context.mutedColor,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                Expanded(
                  child: Text(
                    title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style:
                        theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
                  ),
                ),
                const SizedBox(width: 10),
                Text(value, style: theme.textTheme.bodyMedium?.merge(AppTheme.amount)),
              ],
            ),
            const SizedBox(height: 4),
            Row(
              children: <Widget>[
                const SizedBox(width: 18),
                Expanded(
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(999),
                    child: SizedBox(
                      height: 5,
                      child: Stack(
                        children: <Widget>[
                          Container(color: context.surfaceColor),
                          FractionallySizedBox(
                            widthFactor:
                                fraction.isFinite ? fraction.clamp(0.02, 1.0) : 0.02,
                            child: Container(color: bar),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
                if (subtitle != null || share != null) ...<Widget>[
                  const SizedBox(width: 10),
                  SizedBox(
                    width: 96,
                    child: Text(
                      <String?>[share, subtitle].whereType<String>().join(' · '),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      textAlign: TextAlign.end,
                      style:
                          theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
                    ),
                  ),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// One cell of a [StatStrip].
class Stat {
  const Stat({
    required this.label,
    required this.value,
    this.colour,
    this.trailing,
    this.onTap,
  });

  final String label;
  final String value;
  final Color? colour;

  /// A chip or dot that belongs beside the figure -- a change percentage, most
  /// often.
  final Widget? trailing;

  final VoidCallback? onTap;
}

/// A row of small figures, separated by hairlines.
///
/// The densest honest way to put three or four related numbers on one line, and
/// the reason a card can carry a chart *and* the figures behind it without
/// becoming a screen of its own.
class StatStrip extends StatelessWidget {
  const StatStrip({super.key, required this.stats, this.padding});

  final List<Stat> stats;
  final EdgeInsets? padding;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Padding(
      padding: padding ?? const EdgeInsets.symmetric(horizontal: 16),
      child: IntrinsicHeight(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            for (int i = 0; i < stats.length; i++) ...<Widget>[
              if (i > 0)
                Container(
                  width: 1,
                  margin: const EdgeInsets.symmetric(horizontal: 10, vertical: 2),
                  color: context.lineColor,
                ),
              Expanded(
                child: InkWell(
                  onTap: stats[i].onTap,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: <Widget>[
                      Text(
                        stats[i].label,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.labelSmall
                            ?.copyWith(color: context.mutedColor),
                      ),
                      const SizedBox(height: 2),
                      Row(
                        children: <Widget>[
                          // A cell may carry only a trailing chip -- the change
                          // column does exactly that. An empty string measures
                          // zero wide, and `FittedBox` asserts on a zero-width
                          // child, which takes down the whole card and every
                          // sliver after it rather than drawing one blank cell.
                          if (stats[i].value.isNotEmpty)
                            Flexible(
                              child: FittedBox(
                                fit: BoxFit.scaleDown,
                                alignment: Alignment.centerLeft,
                                child: Text(
                                  stats[i].value,
                                  style: theme.textTheme.titleSmall
                                      ?.merge(AppTheme.amount)
                                      .copyWith(color: stats[i].colour),
                                ),
                              ),
                            ),
                          if (stats[i].trailing != null) ...<Widget>[
                            if (stats[i].value.isNotEmpty) const SizedBox(width: 5),
                            stats[i].trailing!,
                          ],
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
