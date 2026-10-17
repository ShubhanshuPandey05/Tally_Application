/// The two-digit state code a GST document prints beside the state's name.
///
/// A GSTIN begins with it, so a registered business needs no table. This one
/// is for the business that has a state on its master and no GSTIN -- an
/// unregistered buyer, or a company that never entered its number -- where
/// Tally's own invoice still prints "State Name : Gujarat, Code : 24".
abstract final class GstState {
  /// The code for a party, from its GSTIN when it has one and its state when
  /// it does not. Null when neither gives an answer; a guessed code on a tax
  /// document is worse than a missing one.
  static String? code({String? gstin, String? state}) {
    final String number = (gstin ?? '').trim();
    if (number.length >= 2 && int.tryParse(number.substring(0, 2)) != null) {
      return number.substring(0, 2);
    }
    return _codes[_fold(state ?? '')];
  }

  /// Shops and Tally releases spell these differently -- "Jammu & Kashmir" and
  /// "Jammu and Kashmir", "Orissa" and "Odisha" -- so names are matched on
  /// their letters alone.
  static String _fold(String name) => name
      .toLowerCase()
      .replaceAll('&', 'and')
      .replaceAll(RegExp('[^a-z]'), '');

  static const Map<String, String> _codes = <String, String>{
    'jammuandkashmir': '01',
    'himachalpradesh': '02',
    'punjab': '03',
    'chandigarh': '04',
    'uttarakhand': '05',
    'uttaranchal': '05',
    'haryana': '06',
    'delhi': '07',
    'rajasthan': '08',
    'uttarpradesh': '09',
    'bihar': '10',
    'sikkim': '11',
    'arunachalpradesh': '12',
    'nagaland': '13',
    'manipur': '14',
    'mizoram': '15',
    'tripura': '16',
    'meghalaya': '17',
    'assam': '18',
    'westbengal': '19',
    'jharkhand': '20',
    'odisha': '21',
    'orissa': '21',
    'chhattisgarh': '22',
    'madhyapradesh': '23',
    'gujarat': '24',
    'dadraandnagarhavelianddamananddiu': '26',
    'maharashtra': '27',
    'karnataka': '29',
    'goa': '30',
    'lakshadweep': '31',
    'kerala': '32',
    'tamilnadu': '33',
    'puducherry': '34',
    'pondicherry': '34',
    'andamanandnicobarislands': '35',
    'telangana': '36',
    'andhrapradesh': '37',
    'ladakh': '38',
  };
}
