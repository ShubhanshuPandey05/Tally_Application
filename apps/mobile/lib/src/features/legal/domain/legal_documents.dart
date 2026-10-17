/// The Terms of Use and the Privacy Policy, as data the app renders.
///
/// Held in the app rather than fetched, because both are linked from the
/// sign-up screen: a person must be able to read what they are agreeing to
/// with no account, no session and possibly no signal. A wording change ships
/// with a release, and [LegalDocument.updated] says which version they read.
///
/// The text describes what the product actually does -- read-mostly, the one
/// write path through optional vouchers, what the connector sends and what
/// the server keeps -- so a change to any of those is a change to this file.
library;

/// The business behind the product. Every value in square brackets is a
/// placeholder the owner has to supply before release.
class LegalDetails {
  const LegalDetails._();

  static const String productName = 'Prime Analyst';
  static const String legalName = '[Company legal name]';
  static const String address = '[Registered office address]';
  static const String contactEmail = '[contact email]';
  static const String grievanceOfficer = '[Grievance Officer name]';
  static const String grievanceEmail = '[grievance email]';
  static const String jurisdictionCity = '[City]';
  static const String serverLocation = '[server location]';
}

class LegalSection {
  const LegalSection(this.heading, this.paragraphs);

  final String heading;

  /// Each entry is one paragraph. An entry starting with "• " is drawn as a
  /// bullet, so a list reads as a list without a markup parser in the app.
  final List<String> paragraphs;
}

class LegalDocument {
  const LegalDocument({
    required this.title,
    required this.updated,
    required this.intro,
    required this.sections,
  });

  final String title;

  /// Shown as "Last updated", in the form people write dates in India.
  final String updated;
  final String intro;
  final List<LegalSection> sections;
}

const String _p = LegalDetails.productName;
const String _us = LegalDetails.legalName;

const LegalDocument termsOfUse = LegalDocument(
  title: 'Terms of Use',
  updated: '17 October 2026',
  intro: 'These terms are an agreement between you and $_us ("we", "us"), the '
      'provider of $_p. They cover the $_p mobile app, its web version, the '
      '$_p Connector for Windows and the services behind them. By creating an '
      'account or using $_p you accept these terms. If you use it for a '
      'business, you accept them on behalf of that business.',
  sections: <LegalSection>[
    LegalSection('1. What $_p is', <String>[
      '$_p shows reports, dashboards and analytics from your TallyPrime books on '
          'your phone. It is a companion to TallyPrime, not accounting software '
          'and not a replacement for it. Your books stay in TallyPrime.',
      'TallyPrime is a trademark of Tally Solutions Pvt. Ltd. $_p is an '
          'independent product and is not affiliated with or endorsed by Tally '
          'Solutions.',
    ]),
    LegalSection('2. Your account', <String>[
      'You must give accurate details when you sign up and keep your password '
          'to yourself. You are responsible for everything done with your '
          'account and by the people you add to it.',
      'One account is for one business. The account admin decides who else can '
          'sign in and which companies each person can see. Tell us straight '
          'away if you think someone else has used your account; "Sign out on '
          'all devices" in Profile ends every session at once.',
      'A new account is reviewed before it can connect a Tally PC or add '
          'people. Until then you can explore a sample company that is not real '
          'business data.',
    ]),
    LegalSection('3. Connecting TallyPrime', <String>[
      'To show your figures, you install the $_p Connector on the PC that runs '
          'TallyPrime. The connector reads from TallyPrime on that PC and sends '
          'the data to our servers over an encrypted connection. It never opens '
          'a port on your computer or network.',
      'You confirm that you are allowed to connect the companies you add, and '
          'to let the people on your account see them.',
      'Reading data puts some load on TallyPrime. The connector waits its turn '
          'and you can slow it down from its own window, but you should not '
          'expect TallyPrime to be as fast while a large first sync is running.',
    ]),
    LegalSection('4. Entries made from the app', <String>[
      'The app can create new receipts, payments, sales, purchases, sales orders '
          'and purchase orders. It cannot edit or delete anything already in '
          'your books.',
      'By default each new entry arrives in TallyPrime as an optional voucher. '
          'It changes no balance or report until someone approves it inside '
          'TallyPrime. Whoever sets the connector to post entries directly '
          'accepts that entries from the app will then count straight away.',
      'You are responsible for checking and approving the entries you or your '
          'team make. If the app says an entry may or may not have been saved, '
          'check the Day Book in TallyPrime before entering it again, or it may '
          'be recorded twice.',
    ]),
    LegalSection('5. Figures are for information', <String>[
      'We work hard to show your figures exactly as TallyPrime holds them, but '
          'what you see is a copy and may be out of date, for example when your '
          'PC is off. The app tells you when figures were last updated.',
      'TallyPrime is the record. Check it before filing a return, paying tax, '
          'or making a decision that depends on an exact figure. $_p does not '
          'give accounting, tax or legal advice.',
    ]),
    LegalSection('6. Acceptable use', <String>[
      'You agree not to:',
      '• use $_p for anything unlawful, or for data you have no right to use;',
      '• try to reach another business\'s data, or test, probe or break our '
          'security;',
      '• copy, resell, reverse engineer or build a competing service from $_p, '
          'except where the law allows it;',
      '• overload our servers or interfere with other customers\' use.',
    ]),
    LegalSection('7. Plans and payment', <String>[
      'What your account covers, such as the number of companies and users, '
          'and any fees, are set when your account is approved and may be '
          'agreed with us or with one of our partners. If a subscription lapses '
          'or is suspended, you may lose access to your data in the app until '
          'it is renewed. Your books in TallyPrime are never affected.',
    ]),
    LegalSection('8. Your data', <String>[
      'Your business data stays yours. You give us permission to store and '
          'process it only to run $_p for you. How we handle it is described in '
          'our Privacy Policy, which is part of these terms.',
    ]),
    LegalSection('9. Updates and changes', <String>[
      'We update the app and the connector to fix problems and add features. '
          'Some updates may be required to keep using the service. We may '
          'change, suspend or stop a feature, and will give reasonable notice '
          'of changes that matter to you where we can.',
      'We may update these terms. When we do, we will change the date above '
          'and, for significant changes, tell you in the app. Using $_p after '
          'that means you accept the new terms.',
    ]),
    LegalSection('10. Ending the agreement', <String>[
      'You can stop using $_p at any time and ask us to close your account. We '
          'may suspend or close an account that breaks these terms or puts the '
          'service or other customers at risk. When an account closes we delete '
          'its data as set out in the Privacy Policy.',
    ]),
    LegalSection('11. Liability', <String>[
      '$_p is provided "as is". To the extent the law allows, we do not promise '
          'that it will always be available, error-free or suitable for a '
          'particular purpose.',
      'To the extent the law allows, we are not liable for indirect or '
          'consequential losses, lost profits, or decisions made on figures '
          'shown in the app, and our total liability for any claim is limited '
          'to the fees you paid us in the 12 months before the claim. Nothing '
          'here limits liability that cannot be limited by law.',
    ]),
    LegalSection('12. Law and disputes', <String>[
      'These terms are governed by the laws of India. The courts at '
          '${LegalDetails.jurisdictionCity} have exclusive jurisdiction over '
          'any dispute about them. Please contact us first; most problems can '
          'be solved without going that far.',
    ]),
    LegalSection('13. Contact', <String>[
      _us,
      LegalDetails.address,
      'Email: ${LegalDetails.contactEmail}',
    ]),
  ],
);

const LegalDocument privacyPolicy = LegalDocument(
  title: 'Privacy Policy',
  updated: '17 October 2026',
  intro: 'This policy explains what $_p collects, why, where it is kept and '
      'what you can ask us to do with it. It is written to meet the Digital '
      'Personal Data Protection Act, 2023 and the Information Technology Act, '
      '2000 and its rules. $_us is responsible for the data described here.',
  sections: <LegalSection>[
    LegalSection('1. What we collect', <String>[
      'About you and your team:',
      '• your name, email address and business name, and a password we store '
          'only as a one-way hash;',
      '• who belongs to your account, their role, and which companies each '
          'person can see;',
      '• the devices you sign in on (the app version, platform and a short '
          'device description), so you can see and end your sessions.',
      'From TallyPrime, through the connector on your PC:',
      '• company details, such as name and the years your books cover;',
      '• ledgers, groups, balances, stock items, stock levels, godowns and '
          'outstanding bills;',
      '• vouchers and their entries, including party names, amounts, dates, '
          'narrations and GST details;',
      '• the PC\'s name, Windows version and connector version, and the '
          'connector\'s own diagnostic log.',
      'From using the app:',
      '• a record of requests made to our servers, with time and IP address, '
          'for security and troubleshooting;',
      '• daily counts of activity per person (for example, how many reports '
          'were opened). These counts never include which report, which '
          'company or any figure.',
    ]),
    LegalSection('2. What we do not collect', <String>[
      'We do not collect your contacts, photos, files or location. The app uses '
          'the camera only to scan the pairing code shown by the connector, and '
          'the image is not stored or sent anywhere. We do not use advertising '
          'or third-party tracking, and we do not sell your data.',
    ]),
    LegalSection('3. Why we use it', <String>[
      '• to build your dashboards and reports and keep them up to date;',
      '• to send entries you create in the app to your TallyPrime;',
      '• to sign you in, keep your account secure and keep each business\'s '
          'data separate from every other;',
      '• to find and fix problems, and to know whether the product is being '
          'used, so we can support you and improve it;',
      '• to meet our legal obligations.',
      'We use your data because you ask us to provide the service when you sign '
          'up and connect TallyPrime. You can withdraw that at any time by '
          'disconnecting your PC or closing your account.',
    ]),
    LegalSection('4. Where it is kept and how it is protected', <String>[
      'On your PC: the connector reads from TallyPrime on the same machine and '
          'keeps data in memory only. Nothing is opened to the internet; the '
          'connector makes the connection outwards.',
      'In transit: everything between the connector, our servers and the app '
          'travels over encrypted connections (TLS).',
      'On our servers: your business data is encrypted at rest with AES-256. '
          'Each company has its own key, and those keys are themselves '
          'protected by a master key held in a key management service (AWS '
          'KMS), separately from the database. A copy of the database on its '
          'own cannot be read. Our servers decrypt a company\'s data only to '
          'answer a request from someone allowed to see it.',
      'On your phone: reports kept for offline use are stored encrypted with '
          'AES-256, with the key held in the phone\'s secure storage (Android '
          'Keystore or the iOS Keychain). Sign-in tokens are kept in the same '
          'secure storage.',
      'Our servers are located in ${LegalDetails.serverLocation}. Only a small number of '
          'our staff can reach them, and only to run and support the service.',
    ]),
    LegalSection('5. Who can see it', <String>[
      '• People on your account, and only for the companies the account admin '
          'gives them.',
      '• Our support staff and, where your account is managed by one of our '
          'partners, that partner. Our management tools show how much of the '
          'product an account uses, never the figures in your books.',
      '• Service providers who host and run our infrastructure for us, under '
          'contracts that let them use it only to provide that service.',
      '• Authorities, when the law requires us to share it.',
    ]),
    LegalSection('6. How long we keep it', <String>[
      '• Your business data: for as long as the company is connected to your '
          'account. When you remove a company we keep its history so it can be '
          'brought back if you add it again; ask us and we will delete it.',
      '• Request records and diagnostic logs: two days.',
      '• Daily activity counts: 180 days.',
      '• Entries waiting to reach a PC that is switched off: seven days, then '
          'they expire.',
      '• Account details: until the account is closed, and then only as long '
          'as the law requires.',
    ]),
    LegalSection('7. Your rights', <String>[
      'You can ask us to:',
      '• tell you what personal data we hold about you and how we use it;',
      '• correct or complete it;',
      '• delete it, and close your account;',
      '• name someone who can exercise these rights for you if you cannot.',
      'You can also withdraw your consent at any time. Write to the Grievance '
          'Officer below. We will reply within 30 days. If you are not '
          'satisfied, you can complain to the Data Protection Board of India.',
    ]),
    LegalSection('8. Children', <String>[
      '$_p is a business tool and is not meant for anyone under 18. We do not '
          'knowingly collect data about children.',
    ]),
    LegalSection('9. Changes to this policy', <String>[
      'If we change how we handle your data, we will update this policy and the '
          'date above, and tell you in the app before a significant change '
          'takes effect.',
    ]),
    LegalSection('10. Grievance Officer', <String>[
      LegalDetails.grievanceOfficer,
      '$_us, ${LegalDetails.address}',
      'Email: ${LegalDetails.grievanceEmail}',
    ]),
  ],
);
