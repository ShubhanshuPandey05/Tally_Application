import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/features/reports/domain/reports.dart';

Map<String, Object?> _money(String amount) => <String, Object?>{
      'amount': amount,
      'side': 'debit',
      'signed': amount,
      'currency': 'INR',
      'display': '$amount Dr',
    };

void main() {
  test('a godown of mixed units carries a value but no quantity', () {
    final GodownReport report = GodownReport.fromJson(<String, Object?>{
      'value': _money('40000.00'),
      'godown_count': 2,
      'godowns': <Object?>[
        <String, Object?>{
          'name': 'Back Store',
          'value': _money('24000.00'),
          'quantity': 300.0,
          'unit': 'KG',
          'rate': _money('80.00'),
          'items': <Object?>[
            <String, Object?>{
              'name': 'Rice',
              'quantity': 300.0,
              'unit': 'KG',
              'rate': _money('80.00'),
              'value': _money('24000.00'),
              'is_negative': false,
            },
          ],
        },
        <String, Object?>{
          'name': 'Shop',
          'value': _money('16000.00'),
          'quantity': null,
          'unit': null,
          'rate': null,
          'items': <Object?>[
            <String, Object?>{
              'name': 'Oil',
              'quantity': -3.0,
              'unit': 'LTR',
              'rate': null,
              'value': _money('0.00'),
              'is_negative': true,
            },
          ],
        },
      ],
    });

    expect(report.godowns.map((GodownHolding g) => g.name), <String>['Back Store', 'Shop']);
    expect(report.godowns.first.quantity, 300);
    expect(report.godowns.first.rate, isNotNull);

    final GodownHolding shop = report.godowns.last;
    expect(shop.quantity, isNull);
    expect(shop.rate, isNull);
    expect(shop.items.single.isNegative, isTrue);
    expect(shop.items.single.rate, isNull);
  });
}
