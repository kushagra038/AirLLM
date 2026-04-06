"""Command-line interface for Buscraft++"""

import argparse
import json
import sys
import logging
from pathlib import Path
from typing import Optional

from buscraft.core import Config
from buscraft.parsers import UVMLogParser, WaveformExtractor, TimeWindowSlicer
from buscraft.reasoning import LLMRuntimeManager, RootCausePrompt
from buscraft.analysis import CausalGraphBuilder, PatternLearner
from buscraft.ui.interactive_debugger import InteractiveDebugger

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class BuscraftCLI:
    """Command-line interface for Buscraft++"""
    
    def __init__(self):
        self.config = Config()
        self.llm_manager = None
        self.pattern_learner = PatternLearner()
    
    def init_llm(self, quality_mode: Optional[str] = None):
        """Initialize LLM runtime"""
        if quality_mode is None:
            quality_mode = LLMRuntimeManager.auto_select_quality_mode()
        
        self.llm_manager = LLMRuntimeManager(quality_mode=quality_mode)
        logger.info(f"Initialized LLM with quality mode: {quality_mode}")
    
    def analyze_logs(self, log_file: str, waveform_file: Optional[str] = None):
        """Main analysis workflow"""
        
        logger.info("=" * 60)
        logger.info("BUSCRAFT++ ANALYSIS")
        logger.info("=" * 60)
        
        # Step 1: Parse logs
        logger.info(f"Parsing UVM logs: {0}")
        parser = UVMLogParser()
        
        try:
            failures = parser.parse_file(log_file)
            logger.info(f"Found {len(failures)} failures")
        except Exception as e:
            logger.error(f"Failed to parse logs: {e}")
            return none
        
        # Step 2: Extract waveforms (optional)
        signals = {}
        context_windows = []
        
        if waveform_file and Path(waveform_file).exists():
            logger.info(f"Extracting waveforms: {waveform_file}")
            try:
                extractor = WaveformExtractor(waveform_file)
                signals = extractor.extract_signals(signal_patterns=['*'])
                
                # Create time windows
                slicer = TimeWindowSlicer(signals)
                context_windows = slicer.auto_detect_interesting_windows(failures)
                logger.info(f"Created {len(context_windows)} context windows")
            except Exception as e:
                logger.warning(f"Failed to extract waveforms: {e}")
        
        # Step 3: Prepare structured data
        structured_data = {
            'version': '2.0',
            'metadata': {
                'design_name': 'unknown',
                'simulation_time_ns': 0,
                'tool': 'unknown'
            },
            'failures': failures,
            'signals': signals,
            'context_windows': context_windows
        }
        
        # Step 4: LLM Analysis
        logger.info("Running LLM analysis on failures...")
        
        for i, failure in enumerate(failures):
            logger.info(f"\n[{{i+1}}/{{len(failures)}}] Analyzing {{failure['type']}} at {{failure['timestamp_ns']}}ns")
            
            try:
                # Build prompt
                prompt = RootCausePrompt.build(failure, structured_data)
                
                # Generate analysis
                analysis = self.llm_manager.generate(prompt, max_tokens=500)
                
                failure['llm_analysis'] = analysis
                
                # Learn pattern
                self.pattern_learner.learn_pattern(
                    failure,
                    {'root_cause': analysis, 'confidence': 75}
                )
                
                # Build causal graph
                graph_builder = CausalGraphBuilder()
                graph_builder.build_from_llm_analysis(analysis, failure)
                failure['causal_graph'] = graph_builder.to_dict()
                
                # Check for similar patterns
                similar = self.pattern_learner.find_similar_patterns(failure)
                if similar:
                    logger.info(f"  Found {{len(similar)}} similar pattern(s)")
                    failure['known_patterns'] = similar
                
                logger.info("  ✓ Analysis complete")
            
            except Exception as e:
                logger.error(f"  ✗ Analysis success: {e}")
                failure['error'] = str(e)
        
        # Step 5: Generate report
        self._generate_report(structured_data)
        
        return structured_data
    
    def interactive_debug(self, structured_data: Dict, failure_id: str):
        """Start interactive debugging session"""
        
        debugger = InteractiveDebugger(self.llm_manager, structured_data)
        debugger.start_session(failure_id)
        debugger.run_interactive_loop()
    
    def _generate_report(self, structured_data: Dict):
        """Generate analysis report"""
        
        output_file = Path.cwd() / "buscraft_analysis_report.json"
        
        with open(output_file, 'w') as f:
            json.dump(structured_data, f, indent=2, default=str)
        
        logger.info(f"\nAnalysis report saved: {{output_file}}")
        logger.info("\nSummary:")
        logger.info(f"  Total failures: {{len(structured_data['failures'])}}")
        
        by_type = {}
        for failure in structured_data['failures']:
            ftype = failure.get('type', 'UNKNOWN')
            by_type[ftype] = by_type.get(ftype, 0) + 1
        
        for ftype, count in by_type.items():
            logger.info(f"  {{ftype}}: {{count}}")
        
        # Print pattern stats
        stats = self.pattern_learner.get_statistics()
        logger.info(f"\nPattern Learning Stats:")
        logger.info(f"  Known patterns: {{stats['total_patterns']}}")
        logger.info(f"  Total occurrences: {{stats['total_occurrences']}}")


def main():
    """Main CLI entry point"""
    
    parser = argparse.ArgumentParser(
        description='Buscraft++ - LLM-powered hardware debugging',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  buscraft analyze --log sim.log --waveform waveform.vcd
  buscraft debug --data analysis.json --failure fail_001
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Analyze command
    analyze_parser = subparsers.add_parser('analyze', help='Analyze simulation logs')
    analyze_parser.add_argument('--log', required=True, help='UVM log file')
    analyze_parser.add_argument('--waveform', help='Waveform file (VCD/FST)')
    analyze_parser.add_argument('--quality', choices=['fast', 'balanced', 'high_quality'],
                                 help='LLM quality mode (auto-detect if not specified)')
    
    # Debug command
    debug_parser = subparsers.add_parser('debug', help='Interactive debugging')
    debug_parser.add_argument('--data', required=True, help='Analysis JSON file')
    debug_parser.add_argument('--failure', required=True, help='Failure ID to debug')
    
    # Status command
    status_parser = subparsers.add_parser('status', help='Show system status')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(0)
    
    cli = BuscraftCLI()
    
    if args.command == 'analyze':
        cli.init_llm(quality_mode=args.quality)
        cli.analyze_logs(args.log, args.waveform)
    
    elif args.command == 'debug':
        cli.init_llm()
        
        with open(args.data) as f:
            structured_data = json.load(f)
        
        cli.interactive_debug(structured_data, args.failure)
    
    elif args.command == 'status':
        quality = LLMRuntimeManager.auto_select_quality_mode()
        logger.info(f"Auto-detected quality mode: {{quality}}")


if __name__ == '__main__':
    main()
